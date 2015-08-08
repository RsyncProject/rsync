/*
 * Socket and pipe I/O utilities used in rsync.
 *
 * Copyright (C) 1996-2001 Andrew Tridgell
 * Copyright (C) 1996 Paul Mackerras
 * Copyright (C) 2001, 2002 Martin Pool <mbp@samba.org>
 * Copyright (C) 2003-2015 Wayne Davison
 *
 * This program is free software; you can redistribute it and/or modify
 * it under the terms of the GNU General Public License as published by
 * the Free Software Foundation; either version 3 of the License, or
 * (at your option) any later version.
 *
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License along
 * with this program; if not, visit the http://fsf.org website.
 */

/* Rsync provides its own multiplexing system, which is used to send
 * stderr and stdout over a single socket.
 *
 * For historical reasons this is off during the start of the
 * connection, but it's switched on quite early using
 * io_start_multiplex_out() and io_start_multiplex_in(). */

#include "rsync.h"
#include "ifuncs.h"
#include "inums.h"

/** If no timeout is specified then use a 60 second select timeout */
#define SELECT_TIMEOUT 60

extern int bwlimit;
extern size_t bwlimit_writemax;
extern int io_timeout;
extern int am_server;
extern int am_sender;
extern int am_receiver;
extern int am_generator;
extern int msgs2stderr;
extern int inc_recurse;
extern int io_error;
extern int eol_nulls;
extern int flist_eof;
extern int file_total;
extern int file_old_total;
extern int list_only;
extern int read_batch;
extern int compat_flags;
extern int protect_args;
extern int checksum_seed;
extern int protocol_version;
extern int remove_source_files;
extern int preserve_hard_links;
extern BOOL extra_flist_sending_enabled;
extern BOOL flush_ok_after_signal;
extern struct stats stats;
extern struct file_list *cur_flist;
#ifdef ICONV_OPTION
extern int filesfrom_convert;
extern iconv_t ic_send, ic_recv;
#endif

int csum_length = SHORT_SUM_LENGTH; /* initial value */
int allowed_lull = 0;
int batch_fd = -1;
int msgdone_cnt = 0;
int forward_flist_data = 0;
BOOL flist_receiving_enabled = False;

/* Ignore an EOF error if non-zero. See whine_about_eof(). */
int kluge_around_eof = 0;
int got_kill_signal = -1; /* is set to 0 only after multiplexed I/O starts */

int sock_f_in = -1;
int sock_f_out = -1;

int64 total_data_read = 0;
int64 total_data_written = 0;

static struct {
	xbuf in, out, msg;
	int in_fd;
	int out_fd; /* Both "out" and "msg" go to this fd. */
	int in_multiplexed;
	unsigned out_empty_len;
	size_t raw_data_header_pos;      /* in the out xbuf */
	size_t raw_flushing_ends_before; /* in the out xbuf */
	size_t raw_input_ends_before;    /* in the in xbuf */
} iobuf = { .in_fd = -1, .out_fd = -1 };

static time_t last_io_in;
static time_t last_io_out;

static int write_batch_monitor_in = -1;
static int write_batch_monitor_out = -1;

static int ff_forward_fd = -1;
static int ff_reenable_multiplex = -1;
static char ff_lastchar = '\0';
static xbuf ff_xb = EMPTY_XBUF;
#ifdef ICONV_OPTION
static xbuf iconv_buf = EMPTY_XBUF;
#endif
static int select_timeout = SELECT_TIMEOUT;
static int active_filecnt = 0;
static OFF_T active_bytecnt = 0;
static int first_message = 1;

static char int_byte_extra[64] = {
	0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, /* (00 - 3F)/4 */
	0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, /* (40 - 7F)/4 */
	1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, /* (80 - BF)/4 */
	2, 2, 2, 2, 2, 2, 2, 2, 3, 3, 3, 3, 4, 4, 5, 6, /* (C0 - FF)/4 */
};

/* Our I/O buffers are sized with no bits on in the lowest byte of the "size"
 * (indeed, our rounding of sizes in 1024-byte units assures more than this).
 * This allows the code that is storing bytes near the physical end of a
 * circular buffer to temporarily reduce the buffer's size (in order to make
 * some storing idioms easier), while also making it simple to restore the
 * buffer's actual size when the buffer's "pos" wraps around to the start (we
 * just round the buffer's size up again). */

#define IOBUF_WAS_REDUCED(siz) ((siz) & 0xFF)
#define IOBUF_RESTORE_SIZE(siz) (((siz) | 0xFF) + 1)

#define IN_MULTIPLEXED (iobuf.in_multiplexed != 0)
#define IN_MULTIPLEXED_AND_READY (iobuf.in_multiplexed > 0)
#define OUT_MULTIPLEXED (iobuf.out_empty_len != 0)

#define PIO_NEED_INPUT (1<<0) /* The *_NEED_* flags are mutually exclusive. */
#define PIO_NEED_OUTROOM (1<<1)
#define PIO_NEED_MSGROOM (1<<2)

#define PIO_CONSUME_INPUT (1<<4) /* Must becombined with PIO_NEED_INPUT. */

#define PIO_INPUT_AND_CONSUME (PIO_NEED_INPUT | PIO_CONSUME_INPUT)
#define PIO_NEED_FLAGS (PIO_NEED_INPUT | PIO_NEED_OUTROOM | PIO_NEED_MSGROOM)

#define REMOTE_OPTION_ERROR "rsync: on remote machine: -"
#define REMOTE_OPTION_ERROR2 ": unknown option"

#define FILESFROM_BUFLEN 2048

enum festatus { FES_SUCCESS, FES_REDO, FES_NO_SEND };

static flist_ndx_list redo_list, hlink_list;

static void read_a_msg(void);
static void drain_multiplex_messages(void);
static void sleep_for_bwlimit(int bytes_written);

static void check_timeout(BOOL allow_keepalive, int keepalive_flags)
{
	time_t t, chk;

	/* On the receiving side, the generator is now the one that decides
	 * when a timeout has occurred.  When it is sifting through a lot of
	 * files looking for work, it will be sending keep-alive messages to
	 * the sender, and even though the receiver won't be sending/receiving
	 * anything (not even keep-alive messages), the successful writes to
	 * the sender will keep things going.  If the receiver is actively
	 * receiving data, it will ensure that the generator knows that it is
	 * not idle by sending the generator keep-alive messages (since the
	 * generator might be blocked trying to send checksums, it needs to
	 * know that the receiver is active).  Thus, as long as one or the
	 * other is successfully doing work, the generator will not timeout. */
	if (!io_timeout)
		return;

	t = time(NULL);

	if (allow_keepalive) {
		/* This may put data into iobuf.msg w/o flushing. */
		maybe_send_keepalive(t, keepalive_flags);
	}

	if (!last_io_in)
		last_io_in = t;

	if (am_receiver)
		return;

	chk = MAX(last_io_out, last_io_in);
	if (t - chk >= io_timeout) {
		if (am_server)
			msgs2stderr = 1;
		rprintf(FERROR, "[%s] io timeout after %d seconds -- exiting\n",
			who_am_i(), (int)(t-chk));
		exit_cleanup(RERR_TIMEOUT);
	}
}

/* It's almost always an error to get an EOF when we're trying to read from the
 * network, because the protocol is (for the most part) self-terminating.
 *
 * There is one case for the receiver when it is at the end of the transfer
 * (hanging around reading any keep-alive packets that might come its way): if
 * the sender dies before the generator's kill-signal comes through, we can end
 * up here needing to loop until the kill-signal arrives.  In this situation,
 * kluge_around_eof will be < 0.
 *
 * There is another case for older protocol versions (< 24) where the module
 * listing was not terminated, so we must ignore an EOF error in that case and
 * exit.  In this situation, kluge_around_eof will be > 0. */
static NORETURN void whine_about_eof(BOOL allow_kluge)
{
	if (kluge_around_eof && allow_kluge) {
		int i;
		if (kluge_around_eof > 0)
			exit_cleanup(0);
		/* If we're still here after 10 seconds, exit with an error. */
		for (i = 10*1000/20; i--; )
			msleep(20);
	}

	rprintf(FERROR, RSYNC_NAME ": connection unexpectedly closed "
		"(%s bytes received so far) [%s]\n",
		big_num(stats.total_read), who_am_i());

	exit_cleanup(RERR_STREAMIO);
}

/* Do a safe read, handling any needed looping and error handling.
 * Returns the count of the bytes read, which will only be different
 * from "len" if we encountered an EOF.  This routine is not used on
 * the socket except very early in the transfer. */
static size_t safe_read(int fd, char *buf, size_t len)
{
	size_t got = 0;

	assert(fd != iobuf.in_fd);

	while (1) {
		struct timeval tv;
		fd_set r_fds, e_fds;
		int cnt;

		FD_ZERO(&r_fds);
		FD_SET(fd, &r_fds);
		FD_ZERO(&e_fds);
		FD_SET(fd, &e_fds);
		tv.tv_sec = select_timeout;
		tv.tv_usec = 0;

		cnt = select(fd+1, &r_fds, NULL, &e_fds, &tv);
		if (cnt <= 0) {
			if (cnt < 0 && errno == EBADF) {
				rsyserr(FERROR, errno, "safe_read select failed [%s]",
					who_am_i());
				exit_cleanup(RERR_FILEIO);
			}
			check_timeout(1, MSK_ALLOW_FLUSH);
			continue;
		}

		/*if (FD_ISSET(fd, &e_fds))
			rprintf(FINFO, "select exception on fd %d\n", fd); */

		if (FD_ISSET(fd, &r_fds)) {
			int n = read(fd, buf + got, len - got);
			if (DEBUG_GTE(IO, 2))
				rprintf(FINFO, "[%s] safe_read(%d)=%ld\n", who_am_i(), fd, (long)n);
			if (n == 0)
				break;
			if (n < 0) {
				if (errno == EINTR)
					continue;
				rsyserr(FERROR, errno, "safe_read failed to read %ld bytes [%s]",
					(long)len, who_am_i());
				exit_cleanup(RERR_STREAMIO);
			}
			if ((got += (size_t)n) == len)
				break;
		}
	}

	return got;
}

static const char *what_fd_is(int fd)
{
	static char buf[20];

	if (fd == sock_f_out)
		return "socket";
	else if (fd == iobuf.out_fd)
		return "message fd";
	else if (fd == batch_fd)
		return "batch file";
	else {
		snprintf(buf, sizeof buf, "fd %d", fd);
		return buf;
	}
}

/* Do a safe write, handling any needed looping and error handling.
 * Returns only if everything was successfully written.  This routine
 * is not used on the socket except very early in the transfer. */
static void safe_write(int fd, const char *buf, size_t len)
{
	int n;

	assert(fd != iobuf.out_fd);

	n = write(fd, buf, len);
	if ((size_t)n == len)
		return;
	if (n < 0) {
		if (errno != EINTR && errno != EWOULDBLOCK && errno != EAGAIN) {
		  write_failed:
			rsyserr(FERROR, errno,
				"safe_write failed to write %ld bytes to %s [%s]",
				(long)len, what_fd_is(fd), who_am_i());
			exit_cleanup(RERR_STREAMIO);
		}
	} else {
		buf += n;
		len -= n;
	}

	while (len) {
		struct timeval tv;
		fd_set w_fds;
		int cnt;

		FD_ZERO(&w_fds);
		FD_SET(fd, &w_fds);
		tv.tv_sec = select_timeout;
		tv.tv_usec = 0;

		cnt = select(fd + 1, NULL, &w_fds, NULL, &tv);
		if (cnt <= 0) {
			if (cnt < 0 && errno == EBADF) {
				rsyserr(FERROR, errno, "safe_write select failed on %s [%s]",
					what_fd_is(fd), who_am_i());
				exit_cleanup(RERR_FILEIO);
			}
			if (io_timeout)
				maybe_send_keepalive(time(NULL), MSK_ALLOW_FLUSH);
			continue;
		}

		if (FD_ISSET(fd, &w_fds)) {
			n = write(fd, buf, len);
			if (n < 0) {
				if (errno == EINTR)
					continue;
				goto write_failed;
			}
			buf += n;
			len -= n;
		}
	}
}

/* This is only called when files-from data is known to be available.  We read
 * a chunk of data and put it into the output buffer. */
static void forward_filesfrom_data(void)
{
	int len;

	len = read(ff_forward_fd, ff_xb.buf + ff_xb.len, ff_xb.size - ff_xb.len);
	if (len <= 0) {
		if (len == 0 || errno != EINTR) {
			/* Send end-of-file marker */
			ff_forward_fd = -1;
			write_buf(iobuf.out_fd, "\0\0", ff_lastchar ? 2 : 1);
			free_xbuf(&ff_xb);
			if (ff_reenable_multiplex >= 0)
				io_start_multiplex_out(ff_reenable_multiplex);
		}
		return;
	}

	if (DEBUG_GTE(IO, 2))
		rprintf(FINFO, "[%s] files-from read=%ld\n", who_am_i(), (long)len);

#ifdef ICONV_OPTION
	len += ff_xb.len;
#endif

	if (!eol_nulls) {
		char *s = ff_xb.buf + len;
		/* Transform CR and/or LF into '\0' */
		while (s-- > ff_xb.buf) {
			if (*s == '\n' || *s == '\r')
				*s = '\0';
		}
	}

	if (ff_lastchar)
		ff_xb.pos = 0;
	else {
		char *s = ff_xb.buf;
		/* Last buf ended with a '\0', so don't let this buf start with one. */
		while (len && *s == '\0')
			s++, len--;
		ff_xb.pos = s - ff_xb.buf;
	}

#ifdef ICONV_OPTION
	if (filesfrom_convert && len) {
		char *sob = ff_xb.buf + ff_xb.pos, *s = sob;
		char *eob = sob + len;
		int flags = ICB_INCLUDE_BAD | ICB_INCLUDE_INCOMPLETE | ICB_CIRCULAR_OUT;
		if (ff_lastchar == '\0')
			flags |= ICB_INIT;
		/* Convert/send each null-terminated string separately, skipping empties. */
		while (s != eob) {
			if (*s++ == '\0') {
				ff_xb.len = s - sob - 1;
				if (iconvbufs(ic_send, &ff_xb, &iobuf.out, flags) < 0)
					exit_cleanup(RERR_PROTOCOL); /* impossible? */
				write_buf(iobuf.out_fd, s-1, 1); /* Send the '\0'. */
				while (s != eob && *s == '\0')
					s++;
				sob = s;
				ff_xb.pos = sob - ff_xb.buf;
				flags |= ICB_INIT;
			}
		}

		if ((ff_xb.len = s - sob) == 0)
			ff_lastchar = '\0';
		else {
			/* Handle a partial string specially, saving any incomplete chars. */
			flags &= ~ICB_INCLUDE_INCOMPLETE;
			if (iconvbufs(ic_send, &ff_xb, &iobuf.out, flags) < 0) {
				if (errno == E2BIG)
					exit_cleanup(RERR_PROTOCOL); /* impossible? */
				if (ff_xb.pos)
					memmove(ff_xb.buf, ff_xb.buf + ff_xb.pos, ff_xb.len);
			}
			ff_lastchar = 'x'; /* Anything non-zero. */
		}
	} else
#endif

	if (len) {
		char *f = ff_xb.buf + ff_xb.pos;
		char *t = ff_xb.buf;
		char *eob = f + len;
		/* Eliminate any multi-'\0' runs. */
		while (f != eob) {
			if (!(*t++ = *f++)) {
				while (f != eob && *f == '\0')
					f++;
			}
		}
		ff_lastchar = f[-1];
		if ((len = t - ff_xb.buf) != 0) {
			/* This will not circle back to perform_io() because we only get
			 * called when there is plenty of room in the output buffer. */
			write_buf(iobuf.out_fd, ff_xb.buf, len);
		}
	}
}

void reduce_iobuf_size(xbuf *out, size_t new_size)
{
	if (new_size < out->size) {
		/* Avoid weird buffer interactions by only outputting this to stderr. */
		if (msgs2stderr && DEBUG_GTE(IO, 4)) {
			const char *name = out == &iobuf.out ? "iobuf.out"
					 : out == &iobuf.msg ? "iobuf.msg"
					 : NULL;
			if (name) {
				rprintf(FINFO, "[%s] reduced size of %s (-%d)\n",
					who_am_i(), name, (int)(out->size - new_size));
			}
		}
		out->size = new_size;
	}
}

void restore_iobuf_size(xbuf *out)
{
	if (IOBUF_WAS_REDUCED(out->size)) {
		size_t new_size = IOBUF_RESTORE_SIZE(out->size);
		/* Avoid weird buffer interactions by only outputting this to stderr. */
		if (msgs2stderr && DEBUG_GTE(IO, 4)) {
			const char *name = out == &iobuf.out ? "iobuf.out"
					 : out == &iobuf.msg ? "iobuf.msg"
					 : NULL;
			if (name) {
				rprintf(FINFO, "[%s] restored size of %s (+%d)\n",
					who_am_i(), name, (int)(new_size - out->size));
			}
		}
		out->size = new_size;
	}
}

static void handle_kill_signal(BOOL flush_ok)
{
	got_kill_signal = -1;
	flush_ok_after_signal = flush_ok;
	exit_cleanup(RERR_SIGNAL);
}

/* Perform buffered input and/or output until specified conditions are met.
 * When given a "needed" read or write request, this returns without doing any
 * I/O if the needed input bytes or write space is already available.  Once I/O
 * is needed, this will try to do whatever reading and/or writing is currently
 * possible, up to the maximum buffer allowances, no matter if this is a read
 * or write request.  However, the I/O stops as soon as the required input
 * bytes or output space is available.  If this is not a read request, the
 * routine may also do some advantageous reading of messages from a multiplexed
 * input source (which ensures that we don't jam up with everyone in their
 * "need to write" code and nobody reading the accumulated data that would make
 * writing possible).
 *
 * The iobuf.in, .out and .msg buffers are all circular.  Callers need to be
 * aware that some data copies will need to be split when the bytes wrap around
 * from the end to the start.  In order to help make writing into the output
 * buffers easier for some operations (such as the use of SIVAL() into the
 * buffer) a buffer may be temporarily shortened by a small amount, but the
 * original size will be automatically restored when the .pos wraps to the
 * start.  See also the 3 raw_* iobuf vars that are used in the handling of
 * MSG_DATA bytes as they are read-from/written-into the buffers.
 *
 * When writing, we flush data in the following priority order:
 *
 * 1. Finish writing any in-progress MSG_DATA sequence from iobuf.out.
 *
 * 2. Write out all the messages from the message buf (if iobuf.msg is active).
 *    Yes, this means that a PIO_NEED_OUTROOM call will completely flush any
 *    messages before getting to the iobuf.out flushing (except for rule 1).
 *
 * 3. Write out the raw data from iobuf.out, possibly filling in the multiplexed
 *    MSG_DATA header that was pre-allocated (when output is multiplexed).
 *
 * TODO:  items for possible future work:
 *
 *    - Make this routine able to read the generator-to-receiver batch flow?
 *
 * Unlike the old routines that this replaces, it is OK to read ahead as far as
 * we can because the read_a_msg() routine now reads its bytes out of the input
 * buffer.  In the old days, only raw data was in the input buffer, and any
 * unused raw data in the buf would prevent the reading of socket data. */
static char *perform_io(size_t needed, int flags)
{
	fd_set r_fds, e_fds, w_fds;
	struct timeval tv;
	int cnt, max_fd;
	size_t empty_buf_len = 0;
	xbuf *out;
	char *data;

	if (iobuf.in.len == 0 && iobuf.in.pos != 0) {
		if (iobuf.raw_input_ends_before)
			iobuf.raw_input_ends_before -= iobuf.in.pos;
		iobuf.in.pos = 0;
	}

	switch (flags & PIO_NEED_FLAGS) {
	case PIO_NEED_INPUT:
		/* We never resize the circular input buffer. */
		if (iobuf.in.size < needed) {
			rprintf(FERROR, "need to read %ld bytes, iobuf.in.buf is only %ld bytes.\n",
				(long)needed, (long)iobuf.in.size);
			exit_cleanup(RERR_PROTOCOL);
		}

		if (msgs2stderr && DEBUG_GTE(IO, 3)) {
			rprintf(FINFO, "[%s] perform_io(%ld, %sinput)\n",
				who_am_i(), (long)needed, flags & PIO_CONSUME_INPUT ? "consume&" : "");
		}
		break;

	case PIO_NEED_OUTROOM:
		/* We never resize the circular output buffer. */
		if (iobuf.out.size - iobuf.out_empty_len < needed) {
			fprintf(stderr, "need to write %ld bytes, iobuf.out.buf is only %ld bytes.\n",
				(long)needed, (long)(iobuf.out.size - iobuf.out_empty_len));
			exit_cleanup(RERR_PROTOCOL);
		}

		if (msgs2stderr && DEBUG_GTE(IO, 3)) {
			rprintf(FINFO, "[%s] perform_io(%ld, outroom) needs to flush %ld\n",
				who_am_i(), (long)needed,
				iobuf.out.len + needed > iobuf.out.size
				? (long)(iobuf.out.len + needed - iobuf.out.size) : 0L);
		}
		break;

	case PIO_NEED_MSGROOM:
		/* We never resize the circular message buffer. */
		if (iobuf.msg.size < needed) {
			fprintf(stderr, "need to write %ld bytes, iobuf.msg.buf is only %ld bytes.\n",
				(long)needed, (long)iobuf.msg.size);
			exit_cleanup(RERR_PROTOCOL);
		}

		if (msgs2stderr && DEBUG_GTE(IO, 3)) {
			rprintf(FINFO, "[%s] perform_io(%ld, msgroom) needs to flush %ld\n",
				who_am_i(), (long)needed,
				iobuf.msg.len + needed > iobuf.msg.size
				? (long)(iobuf.msg.len + needed - iobuf.msg.size) : 0L);
		}
		break;

	case 0:
		if (msgs2stderr && DEBUG_GTE(IO, 3))
			rprintf(FINFO, "[%s] perform_io(%ld, %d)\n", who_am_i(), (long)needed, flags);
		break;

	default:
		exit_cleanup(RERR_UNSUPPORTED);
	}

	while (1) {
		switch (flags & PIO_NEED_FLAGS) {
		case PIO_NEED_INPUT:
			if (iobuf.in.len >= needed)
				goto double_break;
			break;
		case PIO_NEED_OUTROOM:
			/* Note that iobuf.out_empty_len doesn't factor into this check
			 * because iobuf.out.len already holds any needed header len. */
			if (iobuf.out.len + needed <= iobuf.out.size)
				goto double_break;
			break;
		case PIO_NEED_MSGROOM:
			if (iobuf.msg.len + needed <= iobuf.msg.size)
				goto double_break;
			break;
		}

		max_fd = -1;

		FD_ZERO(&r_fds);
		FD_ZERO(&e_fds);
		if (iobuf.in_fd >= 0 && iobuf.in.size - iobuf.in.len) {
			if (!read_batch || batch_fd >= 0) {
				FD_SET(iobuf.in_fd, &r_fds);
				FD_SET(iobuf.in_fd, &e_fds);
			}
			if (iobuf.in_fd > max_fd)
				max_fd = iobuf.in_fd;
		}

		/* Only do more filesfrom processing if there is enough room in the out buffer. */
		if (ff_forward_fd >= 0 && iobuf.out.size - iobuf.out.len > FILESFROM_BUFLEN*2) {
			FD_SET(ff_forward_fd, &r_fds);
			if (ff_forward_fd > max_fd)
				max_fd = ff_forward_fd;
		}

		FD_ZERO(&w_fds);
		if (iobuf.out_fd >= 0) {
			if (iobuf.raw_flushing_ends_before
			 || (!iobuf.msg.len && iobuf.out.len > iobuf.out_empty_len && !(flags & PIO_NEED_MSGROOM))) {
				if (OUT_MULTIPLEXED && !iobuf.raw_flushing_ends_before) {
					/* The iobuf.raw_flushing_ends_before value can point off the end
					 * of the iobuf.out buffer for a while, for easier subtracting. */
					iobuf.raw_flushing_ends_before = iobuf.out.pos + iobuf.out.len;

					SIVAL(iobuf.out.buf + iobuf.raw_data_header_pos, 0,
					      ((MPLEX_BASE + (int)MSG_DATA)<<24) + iobuf.out.len - 4);

					if (msgs2stderr && DEBUG_GTE(IO, 1)) {
						rprintf(FINFO, "[%s] send_msg(%d, %ld)\n",
							who_am_i(), (int)MSG_DATA, (long)iobuf.out.len - 4);
					}

					/* reserve room for the next MSG_DATA header */
					iobuf.raw_data_header_pos = iobuf.raw_flushing_ends_before;
					if (iobuf.raw_data_header_pos >= iobuf.out.size)
						iobuf.raw_data_header_pos -= iobuf.out.size;
					else if (iobuf.raw_data_header_pos + 4 > iobuf.out.size) {
						/* The 4-byte header won't fit at the end of the buffer,
						 * so we'll temporarily reduce the output buffer's size
						 * and put the header at the start of the buffer. */
						reduce_iobuf_size(&iobuf.out, iobuf.raw_data_header_pos);
						iobuf.raw_data_header_pos = 0;
					}
					/* Yes, it is possible for this to make len > size for a while. */
					iobuf.out.len += 4;
				}

				empty_buf_len = iobuf.out_empty_len;
				out = &iobuf.out;
			} else if (iobuf.msg.len) {
				empty_buf_len = 0;
				out = &iobuf.msg;
			} else
				out = NULL;
			if (out) {
				FD_SET(iobuf.out_fd, &w_fds);
				if (iobuf.out_fd > max_fd)
					max_fd = iobuf.out_fd;
			}
		} else
			out = NULL;

		if (max_fd < 0) {
			switch (flags & PIO_NEED_FLAGS) {
			case PIO_NEED_INPUT:
				iobuf.in.len = 0;
				if (kluge_around_eof == 2)
					exit_cleanup(0);
				if (iobuf.in_fd == -2)
					whine_about_eof(True);
				rprintf(FERROR, "error in perform_io: no fd for input.\n");
				exit_cleanup(RERR_PROTOCOL);
			case PIO_NEED_OUTROOM:
			case PIO_NEED_MSGROOM:
				msgs2stderr = 1;
				drain_multiplex_messages();
				if (iobuf.out_fd == -2)
					whine_about_eof(True);
				rprintf(FERROR, "error in perform_io: no fd for output.\n");
				exit_cleanup(RERR_PROTOCOL);
			default:
				/* No stated needs, so I guess this is OK. */
				break;
			}
			break;
		}

		if (got_kill_signal > 0)
			handle_kill_signal(True);

		if (extra_flist_sending_enabled) {
			if (file_total - file_old_total < MAX_FILECNT_LOOKAHEAD && IN_MULTIPLEXED_AND_READY)
				tv.tv_sec = 0;
			else {
				extra_flist_sending_enabled = False;
				tv.tv_sec = select_timeout;
			}
		} else
			tv.tv_sec = select_timeout;
		tv.tv_usec = 0;

		cnt = select(max_fd + 1, &r_fds, &w_fds, &e_fds, &tv);

		if (cnt <= 0) {
			if (cnt < 0 && errno == EBADF) {
				msgs2stderr = 1;
				exit_cleanup(RERR_SOCKETIO);
			}
			if (extra_flist_sending_enabled) {
				extra_flist_sending_enabled = False;
				send_extra_file_list(sock_f_out, -1);
				extra_flist_sending_enabled = !flist_eof;
			} else
				check_timeout((flags & PIO_NEED_INPUT) != 0, 0);
			FD_ZERO(&r_fds); /* Just in case... */
			FD_ZERO(&w_fds);
		}

		if (iobuf.in_fd >= 0 && FD_ISSET(iobuf.in_fd, &r_fds)) {
			size_t len, pos = iobuf.in.pos + iobuf.in.len;
			int n;
			if (pos >= iobuf.in.size) {
				pos -= iobuf.in.size;
				len = iobuf.in.size - iobuf.in.len;
			} else
				len = iobuf.in.size - pos;
			if ((n = read(iobuf.in_fd, iobuf.in.buf + pos, len)) <= 0) {
				if (n == 0) {
					/* Signal that input has become invalid. */
					if (!read_batch || batch_fd < 0 || am_generator)
						iobuf.in_fd = -2;
					batch_fd = -1;
					continue;
				}
				if (errno == EINTR || errno == EWOULDBLOCK || errno == EAGAIN)
					n = 0;
				else {
					/* Don't write errors on a dead socket. */
					if (iobuf.in_fd == sock_f_in) {
						if (am_sender)
							msgs2stderr = 1;
						rsyserr(FERROR_SOCKET, errno, "read error");
					} else
						rsyserr(FERROR, errno, "read error");
					exit_cleanup(RERR_SOCKETIO);
				}
			}
			if (msgs2stderr && DEBUG_GTE(IO, 2))
				rprintf(FINFO, "[%s] recv=%ld\n", who_am_i(), (long)n);

			if (io_timeout) {
				last_io_in = time(NULL);
				if (flags & PIO_NEED_INPUT)
					maybe_send_keepalive(last_io_in, 0);
			}
			stats.total_read += n;

			iobuf.in.len += n;
		}

		if (out && FD_ISSET(iobuf.out_fd, &w_fds)) {
			size_t len = iobuf.raw_flushing_ends_before ? iobuf.raw_flushing_ends_before - out->pos : out->len;
			int n;

			if (bwlimit_writemax && len > bwlimit_writemax)
				len = bwlimit_writemax;

			if (out->pos + len > out->size)
				len = out->size - out->pos;
			if ((n = write(iobuf.out_fd, out->buf + out->pos, len)) <= 0) {
				if (errno == EINTR || errno == EWOULDBLOCK || errno == EAGAIN)
					n = 0;
				else {
					/* Don't write errors on a dead socket. */
					msgs2stderr = 1;
					iobuf.out_fd = -2;
					iobuf.out.len = iobuf.msg.len = iobuf.raw_flushing_ends_before = 0;
					rsyserr(FERROR_SOCKET, errno, "[%s] write error", who_am_i());
					drain_multiplex_messages();
					exit_cleanup(RERR_SOCKETIO);
				}
			}
			if (msgs2stderr && DEBUG_GTE(IO, 2)) {
				rprintf(FINFO, "[%s] %s sent=%ld\n",
					who_am_i(), out == &iobuf.out ? "out" : "msg", (long)n);
			}

			if (io_timeout)
				last_io_out = time(NULL);
			stats.total_written += n;

			if (bwlimit_writemax)
				sleep_for_bwlimit(n);

			if ((out->pos += n) == out->size) {
				if (iobuf.raw_flushing_ends_before)
					iobuf.raw_flushing_ends_before -= out->size;
				out->pos = 0;
				restore_iobuf_size(out);
			} else if (out->pos == iobuf.raw_flushing_ends_before)
				iobuf.raw_flushing_ends_before = 0;
			if ((out->len -= n) == empty_buf_len) {
				out->pos = 0;
				restore_iobuf_size(out);
				if (empty_buf_len)
					iobuf.raw_data_header_pos = 0;
			}
		}

		if (got_kill_signal > 0)
			handle_kill_signal(True);

		/* We need to help prevent deadlock by doing what reading
		 * we can whenever we are here trying to write. */
		if (IN_MULTIPLEXED_AND_READY && !(flags & PIO_NEED_INPUT)) {
			while (!iobuf.raw_input_ends_before && iobuf.in.len > 512)
				read_a_msg();
			if (flist_receiving_enabled && iobuf.in.len > 512)
				wait_for_receiver(); /* generator only */
		}

		if (ff_forward_fd >= 0 && FD_ISSET(ff_forward_fd, &r_fds)) {
			/* This can potentially flush all output and enable
			 * multiplexed output, so keep this last in the loop
			 * and be sure to not cache anything that would break
			 * such a change. */
			forward_filesfrom_data();
		}
	}
  double_break:

	if (got_kill_signal > 0)
		handle_kill_signal(True);

	data = iobuf.in.buf + iobuf.in.pos;

	if (flags & PIO_CONSUME_INPUT) {
		iobuf.in.len -= needed;
		iobuf.in.pos += needed;
		if (iobuf.in.pos == iobuf.raw_input_ends_before)
			iobuf.raw_input_ends_before = 0;
		if (iobuf.in.pos >= iobuf.in.size) {
			iobuf.in.pos -= iobuf.in.size;
			if (iobuf.raw_input_ends_before)
				iobuf.raw_input_ends_before -= iobuf.in.size;
		}
	}

	return data;
}

static void raw_read_buf(char *buf, size_t len)
{
	size_t pos = iobuf.in.pos;
	char *data = perform_io(len, PIO_INPUT_AND_CONSUME);
	if (iobuf.in.pos <= pos && len) {
		size_t siz = len - iobuf.in.pos;
		memcpy(buf, data, siz);
		memcpy(buf + siz, iobuf.in.buf, iobuf.in.pos);
	} else
		memcpy(buf, data, len);
}

static int32 raw_read_int(void)
{
	char *data, buf[4];
	if (iobuf.in.size - iobuf.in.pos >= 4)
		data = perform_io(4, PIO_INPUT_AND_CONSUME);
	else
		raw_read_buf(data = buf, 4);
	return IVAL(data, 0);
}

void noop_io_until_death(void)
{
	char buf[1024];

	if (!iobuf.in.buf || !iobuf.out.buf || iobuf.in_fd < 0 || iobuf.out_fd < 0 || kluge_around_eof)
		return;

	kluge_around_eof = 2;
	/* Setting an I/O timeout ensures that if something inexplicably weird
	 * happens, we won't hang around forever. */
	if (!io_timeout)
		set_io_timeout(60);

	while (1)
		read_buf(iobuf.in_fd, buf, sizeof buf);
}

/* Buffer a message for the multiplexed output stream.  Is not used for (normal) MSG_DATA. */
int send_msg(enum msgcode code, const char *buf, size_t len, int convert)
{
	char *hdr;
	size_t needed, pos;
	BOOL want_debug = DEBUG_GTE(IO, 1) && convert >= 0 && (msgs2stderr || code != MSG_INFO);

	if (!OUT_MULTIPLEXED)
		return 0;

	if (want_debug)
		rprintf(FINFO, "[%s] send_msg(%d, %ld)\n", who_am_i(), (int)code, (long)len);

	/* When checking for enough free space for this message, we need to
	 * make sure that there is space for the 4-byte header, plus we'll
	 * assume that we may waste up to 3 bytes (if the header doesn't fit
	 * at the physical end of the buffer). */
#ifdef ICONV_OPTION
	if (convert > 0 && ic_send == (iconv_t)-1)
		convert = 0;
	if (convert > 0) {
		/* Ensuring double-size room leaves space for maximal conversion expansion. */
		needed = len*2 + 4 + 3;
	} else
#endif
		needed = len + 4 + 3;
	if (iobuf.msg.len + needed > iobuf.msg.size)
		perform_io(needed, PIO_NEED_MSGROOM);

	pos = iobuf.msg.pos + iobuf.msg.len; /* Must be set after any flushing. */
	if (pos >= iobuf.msg.size)
		pos -= iobuf.msg.size;
	else if (pos + 4 > iobuf.msg.size) {
		/* The 4-byte header won't fit at the end of the buffer,
		 * so we'll temporarily reduce the message buffer's size
		 * and put the header at the start of the buffer. */
		reduce_iobuf_size(&iobuf.msg, pos);
		pos = 0;
	}
	hdr = iobuf.msg.buf + pos;

	iobuf.msg.len += 4; /* Allocate room for the coming header bytes. */

#ifdef ICONV_OPTION
	if (convert > 0) {
		xbuf inbuf;

		INIT_XBUF(inbuf, (char*)buf, len, (size_t)-1);

		len = iobuf.msg.len;
		iconvbufs(ic_send, &inbuf, &iobuf.msg,
			  ICB_INCLUDE_BAD | ICB_INCLUDE_INCOMPLETE | ICB_CIRCULAR_OUT | ICB_INIT);
		if (inbuf.len > 0) {
			rprintf(FERROR, "overflowed iobuf.msg buffer in send_msg");
			exit_cleanup(RERR_UNSUPPORTED);
		}
		len = iobuf.msg.len - len;
	} else
#endif
	{
		size_t siz;

		if ((pos += 4) == iobuf.msg.size)
			pos = 0;

		/* Handle a split copy if we wrap around the end of the circular buffer. */
		if (pos >= iobuf.msg.pos && (siz = iobuf.msg.size - pos) < len) {
			memcpy(iobuf.msg.buf + pos, buf, siz);
			memcpy(iobuf.msg.buf, buf + siz, len - siz);
		} else
			memcpy(iobuf.msg.buf + pos, buf, len);

		iobuf.msg.len += len;
	}

	SIVAL(hdr, 0, ((MPLEX_BASE + (int)code)<<24) + len);

	if (want_debug && convert > 0)
		rprintf(FINFO, "[%s] converted msg len=%ld\n", who_am_i(), (long)len);

	return 1;
}

void send_msg_int(enum msgcode code, int num)
{
	char numbuf[4];

	if (DEBUG_GTE(IO, 1))
		rprintf(FINFO, "[%s] send_msg_int(%d, %d)\n", who_am_i(), (int)code, num);

	SIVAL(numbuf, 0, num);
	send_msg(code, numbuf, 4, -1);
}

static void got_flist_entry_status(enum festatus status, int ndx)
{
	struct file_list *flist = flist_for_ndx(ndx, "got_flist_entry_status");

	if (remove_source_files) {
		active_filecnt--;
		active_bytecnt -= F_LENGTH(flist->files[ndx - flist->ndx_start]);
	}

	if (inc_recurse)
		flist->in_progress--;

	switch (status) {
	case FES_SUCCESS:
		if (remove_source_files)
			send_msg_int(MSG_SUCCESS, ndx);
		/* FALL THROUGH */
	case FES_NO_SEND:
#ifdef SUPPORT_HARD_LINKS
		if (preserve_hard_links) {
			struct file_struct *file = flist->files[ndx - flist->ndx_start];
			if (F_IS_HLINKED(file)) {
				if (status == FES_NO_SEND)
					flist_ndx_push(&hlink_list, -2); /* indicates a failure follows */
				flist_ndx_push(&hlink_list, ndx);
				if (inc_recurse)
					flist->in_progress++;
			}
		}
#endif
		break;
	case FES_REDO:
		if (read_batch) {
			if (inc_recurse)
				flist->in_progress++;
			break;
		}
		if (inc_recurse)
			flist->to_redo++;
		flist_ndx_push(&redo_list, ndx);
		break;
	}
}

/* Note the fds used for the main socket (which might really be a pipe
 * for a local transfer, but we can ignore that). */
void io_set_sock_fds(int f_in, int f_out)
{
	sock_f_in = f_in;
	sock_f_out = f_out;
}

void set_io_timeout(int secs)
{
	io_timeout = secs;
	allowed_lull = (io_timeout + 1) / 2;

	if (!io_timeout || allowed_lull > SELECT_TIMEOUT)
		select_timeout = SELECT_TIMEOUT;
	else
		select_timeout = allowed_lull;

	if (read_batch)
		allowed_lull = 0;
}

static void check_for_d_option_error(const char *msg)
{
	static char rsync263_opts[] = "BCDHIKLPRSTWabceghlnopqrtuvxz";
	char *colon;
	int saw_d = 0;

	if (*msg != 'r'
	 || strncmp(msg, REMOTE_OPTION_ERROR, sizeof REMOTE_OPTION_ERROR - 1) != 0)
		return;

	msg += sizeof REMOTE_OPTION_ERROR - 1;
	if (*msg == '-' || (colon = strchr(msg, ':')) == NULL
	 || strncmp(colon, REMOTE_OPTION_ERROR2, sizeof REMOTE_OPTION_ERROR2 - 1) != 0)
		return;

	for ( ; *msg != ':'; msg++) {
		if (*msg == 'd')
			saw_d = 1;
		else if (*msg == 'e')
			break;
		else if (strchr(rsync263_opts, *msg) == NULL)
			return;
	}

	if (saw_d) {
		rprintf(FWARNING,
		    "*** Try using \"--old-d\" if remote rsync is <= 2.6.3 ***\n");
	}
}

/* This is used by the generator to limit how many file transfers can
 * be active at once when --remove-source-files is specified.  Without
 * this, sender-side deletions were mostly happening at the end. */
void increment_active_files(int ndx, int itemizing, enum logcode code)
{
	while (1) {
		/* TODO: tune these limits? */
		int limit = active_bytecnt >= 128*1024 ? 10 : 50;
		if (active_filecnt < limit)
			break;
		check_for_finished_files(itemizing, code, 0);
		if (active_filecnt < limit)
			break;
		wait_for_receiver();
	}

	active_filecnt++;
	active_bytecnt += F_LENGTH(cur_flist->files[ndx - cur_flist->ndx_start]);
}

int get_redo_num(void)
{
	return flist_ndx_pop(&redo_list);
}

int get_hlink_num(void)
{
	return flist_ndx_pop(&hlink_list);
}

/* When we're the receiver and we have a local --files-from list of names
 * that needs to be sent over the socket to the sender, we have to do two
 * things at the same time: send the sender a list of what files we're
 * processing and read the incoming file+info list from the sender.  We do
 * this by making recv_file_list() call forward_filesfrom_data(), which
 * will ensure that we forward data to the sender until we get some data
 * for recv_file_list() to use. */
void start_filesfrom_forwarding(int fd)
{
	if (protocol_version < 31 && OUT_MULTIPLEXED) {
		/* Older protocols send the files-from data w/o packaging
		 * it in multiplexed I/O packets, so temporarily switch
		 * to buffered I/O to match this behavior. */
		iobuf.msg.pos = iobuf.msg.len = 0; /* Be extra sure no messages go out. */
		ff_reenable_multiplex = io_end_multiplex_out(MPLX_TO_BUFFERED);
	}
	ff_forward_fd = fd;

	alloc_xbuf(&ff_xb, FILESFROM_BUFLEN);
}

/* Read a line into the "buf" buffer. */
int read_line(int fd, char *buf, size_t bufsiz, int flags)
{
	char ch, *s, *eob;

#ifdef ICONV_OPTION
	if (flags & RL_CONVERT && iconv_buf.size < bufsiz)
		realloc_xbuf(&iconv_buf, bufsiz + 1024);
#endif

  start:
#ifdef ICONV_OPTION
	s = flags & RL_CONVERT ? iconv_buf.buf : buf;
#else
	s = buf;
#endif
	eob = s + bufsiz - 1;
	while (1) {
		/* We avoid read_byte() for files because files can return an EOF. */
		if (fd == iobuf.in_fd)
			ch = read_byte(fd);
		else if (safe_read(fd, &ch, 1) == 0)
			break;
		if (flags & RL_EOL_NULLS ? ch == '\0' : (ch == '\r' || ch == '\n')) {
			/* Skip empty lines if dumping comments. */
			if (flags & RL_DUMP_COMMENTS && s == buf)
				continue;
			break;
		}
		if (s < eob)
			*s++ = ch;
	}
	*s = '\0';

	if (flags & RL_DUMP_COMMENTS && (*buf == '#' || *buf == ';'))
		goto start;

#ifdef ICONV_OPTION
	if (flags & RL_CONVERT) {
		xbuf outbuf;
		INIT_XBUF(outbuf, buf, 0, bufsiz);
		iconv_buf.pos = 0;
		iconv_buf.len = s - iconv_buf.buf;
		iconvbufs(ic_recv, &iconv_buf, &outbuf,
			  ICB_INCLUDE_BAD | ICB_INCLUDE_INCOMPLETE | ICB_INIT);
		outbuf.buf[outbuf.len] = '\0';
		return outbuf.len;
	}
#endif

	return s - buf;
}

void read_args(int f_in, char *mod_name, char *buf, size_t bufsiz, int rl_nulls,
	       char ***argv_p, int *argc_p, char **request_p)
{
	int maxargs = MAX_ARGS;
	int dot_pos = 0, argc = 0, request_len = 0;
	char **argv, *p;
	int rl_flags = (rl_nulls ? RL_EOL_NULLS : 0);

#ifdef ICONV_OPTION
	rl_flags |= (protect_args && ic_recv != (iconv_t)-1 ? RL_CONVERT : 0);
#endif

	if (!(argv = new_array(char *, maxargs)))
		out_of_memory("read_args");
	if (mod_name && !protect_args)
		argv[argc++] = "rsyncd";

	if (request_p)
		*request_p = NULL;

	while (1) {
		if (read_line(f_in, buf, bufsiz, rl_flags) == 0)
			break;

		if (argc == maxargs-1) {
			maxargs += MAX_ARGS;
			if (!(argv = realloc_array(argv, char *, maxargs)))
				out_of_memory("read_args");
		}

		if (dot_pos) {
			if (request_p && request_len < 1024) {
				int len = strlen(buf);
				if (request_len)
					request_p[0][request_len++] = ' ';
				if (!(*request_p = realloc_array(*request_p, char, request_len + len + 1)))
					out_of_memory("read_args");
				memcpy(*request_p + request_len, buf, len + 1);
				request_len += len;
			}
			if (mod_name)
				glob_expand_module(mod_name, buf, &argv, &argc, &maxargs);
			else
				glob_expand(buf, &argv, &argc, &maxargs);
		} else {
			if (!(p = strdup(buf)))
				out_of_memory("read_args");
			argv[argc++] = p;
			if (*p == '.' && p[1] == '\0')
				dot_pos = argc;
		}
	}
	argv[argc] = NULL;

	glob_expand(NULL, NULL, NULL, NULL);

	*argc_p = argc;
	*argv_p = argv;
}

BOOL io_start_buffering_out(int f_out)
{
	if (msgs2stderr && DEBUG_GTE(IO, 2))
		rprintf(FINFO, "[%s] io_start_buffering_out(%d)\n", who_am_i(), f_out);

	if (iobuf.out.buf) {
		if (iobuf.out_fd == -1)
			iobuf.out_fd = f_out;
		else
			assert(f_out == iobuf.out_fd);
		return False;
	}

	alloc_xbuf(&iobuf.out, ROUND_UP_1024(IO_BUFFER_SIZE * 2));
	iobuf.out_fd = f_out;

	return True;
}

BOOL io_start_buffering_in(int f_in)
{
	if (msgs2stderr && DEBUG_GTE(IO, 2))
		rprintf(FINFO, "[%s] io_start_buffering_in(%d)\n", who_am_i(), f_in);

	if (iobuf.in.buf) {
		if (iobuf.in_fd == -1)
			iobuf.in_fd = f_in;
		else
			assert(f_in == iobuf.in_fd);
		return False;
	}

	alloc_xbuf(&iobuf.in, ROUND_UP_1024(IO_BUFFER_SIZE));
	iobuf.in_fd = f_in;

	return True;
}

void io_end_buffering_in(BOOL free_buffers)
{
	if (msgs2stderr && DEBUG_GTE(IO, 2)) {
		rprintf(FINFO, "[%s] io_end_buffering_in(IOBUF_%s_BUFS)\n",
			who_am_i(), free_buffers ? "FREE" : "KEEP");
	}

	if (free_buffers)
		free_xbuf(&iobuf.in);
	else
		iobuf.in.pos = iobuf.in.len = 0;

	iobuf.in_fd = -1;
}

void io_end_buffering_out(BOOL free_buffers)
{
	if (msgs2stderr && DEBUG_GTE(IO, 2)) {
		rprintf(FINFO, "[%s] io_end_buffering_out(IOBUF_%s_BUFS)\n",
			who_am_i(), free_buffers ? "FREE" : "KEEP");
	}

	io_flush(FULL_FLUSH);

	if (free_buffers) {
		free_xbuf(&iobuf.out);
		free_xbuf(&iobuf.msg);
	}

	iobuf.out_fd = -1;
}

void maybe_flush_socket(int important)
{
	if (flist_eof && iobuf.out.buf && iobuf.out.len > iobuf.out_empty_len
	 && (important || time(NULL) - last_io_out >= 5))
		io_flush(NORMAL_FLUSH);
}

/* Older rsync versions used to send either a MSG_NOOP (protocol 30) or a
 * raw-data-based keep-alive (protocol 29), both of which implied forwarding of
 * the message through the sender.  Since the new timeout method does not need
 * any forwarding, we just send an empty MSG_DATA message, which works with all
 * rsync versions.  This avoids any message forwarding, and leaves the raw-data
 * stream alone (since we can never be quite sure if that stream is in the
 * right state for a keep-alive message). */
void maybe_send_keepalive(time_t now, int flags)
{
	if (flags & MSK_ACTIVE_RECEIVER)
		last_io_in = now; /* Fudge things when we're working hard on the files. */

	/* Early in the transfer (before the receiver forks) the receiving side doesn't
	 * care if it hasn't sent data in a while as long as it is receiving data (in
	 * fact, a pre-3.1.0 rsync would die if we tried to send it a keep alive during
	 * this time).  So, if we're an early-receiving proc, just return and let the
	 * incoming data determine if we timeout. */
	if (!am_sender && !am_receiver && !am_generator)
		return;

	if (now - last_io_out >= allowed_lull) {
		/* The receiver is special:  it only sends keep-alive messages if it is
		 * actively receiving data.  Otherwise, it lets the generator timeout. */
		if (am_receiver && now - last_io_in >= io_timeout)
			return;

		if (!iobuf.msg.len && iobuf.out.len == iobuf.out_empty_len)
			send_msg(MSG_DATA, "", 0, 0);
		if (!(flags & MSK_ALLOW_FLUSH)) {
			/* Let the caller worry about writing out the data. */
		} else if (iobuf.msg.len)
			perform_io(iobuf.msg.size - iobuf.msg.len + 1, PIO_NEED_MSGROOM);
		else if (iobuf.out.len > iobuf.out_empty_len)
			io_flush(NORMAL_FLUSH);
	}
}

void start_flist_forward(int ndx)
{
	write_int(iobuf.out_fd, ndx);
	forward_flist_data = 1;
}

void stop_flist_forward(void)
{
	forward_flist_data = 0;
}

/* Read a message from a multiplexed source. */
static void read_a_msg(void)
{
	char data[BIGPATHBUFLEN];
	int tag, val;
	size_t msg_bytes;

	/* This ensures that perform_io() does not try to do any message reading
	 * until we've read all of the data for this message.  We should also
	 * try to avoid calling things that will cause data to be written via
	 * perform_io() prior to this being reset to 1. */
	iobuf.in_multiplexed = -1;

	tag = raw_read_int();

	msg_bytes = tag & 0xFFFFFF;
	tag = (tag >> 24) - MPLEX_BASE;

	if (DEBUG_GTE(IO, 1) && msgs2stderr)
		rprintf(FINFO, "[%s] got msg=%d, len=%ld\n", who_am_i(), (int)tag, (long)msg_bytes);

	switch (tag) {
	case MSG_DATA:
		assert(iobuf.raw_input_ends_before == 0);
		/* Though this does not yet read the data, we do mark where in
		 * the buffer the msg data will end once it is read.  It is
		 * possible that this points off the end of the buffer, in
		 * which case the gradual reading of the input stream will
		 * cause this value to wrap around and eventually become real. */
		if (msg_bytes)
			iobuf.raw_input_ends_before = iobuf.in.pos + msg_bytes;
		iobuf.in_multiplexed = 1;
		break;
	case MSG_STATS:
		if (msg_bytes != sizeof stats.total_read || !am_generator)
			goto invalid_msg;
		raw_read_buf((char*)&stats.total_read, sizeof stats.total_read);
		iobuf.in_multiplexed = 1;
		break;
	case MSG_REDO:
		if (msg_bytes != 4 || !am_generator)
			goto invalid_msg;
		val = raw_read_int();
		iobuf.in_multiplexed = 1;
		got_flist_entry_status(FES_REDO, val);
		break;
	case MSG_IO_ERROR:
		if (msg_bytes != 4)
			goto invalid_msg;
		val = raw_read_int();
		iobuf.in_multiplexed = 1;
		io_error |= val;
		if (am_receiver)
			send_msg_int(MSG_IO_ERROR, val);
		break;
	case MSG_IO_TIMEOUT:
		if (msg_bytes != 4 || am_server || am_generator)
			goto invalid_msg;
		val = raw_read_int();
		iobuf.in_multiplexed = 1;
		if (!io_timeout || io_timeout > val) {
			if (INFO_GTE(MISC, 2))
				rprintf(FINFO, "Setting --timeout=%d to match server\n", val);
			set_io_timeout(val);
		}
		break;
	case MSG_NOOP:
		/* Support protocol-30 keep-alive method. */
		if (msg_bytes != 0)
			goto invalid_msg;
		iobuf.in_multiplexed = 1;
		if (am_sender)
			maybe_send_keepalive(time(NULL), MSK_ALLOW_FLUSH);
		break;
	case MSG_DELETED:
		if (msg_bytes >= sizeof data)
			goto overflow;
		if (am_generator) {
			raw_read_buf(data, msg_bytes);
			iobuf.in_multiplexed = 1;
			send_msg(MSG_DELETED, data, msg_bytes, 1);
			break;
		}
#ifdef ICONV_OPTION
		if (ic_recv != (iconv_t)-1) {
			xbuf outbuf, inbuf;
			char ibuf[512];
			int add_null = 0;
			int flags = ICB_INCLUDE_BAD | ICB_INIT;

			INIT_CONST_XBUF(outbuf, data);
			INIT_XBUF(inbuf, ibuf, 0, (size_t)-1);

			while (msg_bytes) {
				size_t len = msg_bytes > sizeof ibuf - inbuf.len ? sizeof ibuf - inbuf.len : msg_bytes;
				raw_read_buf(ibuf + inbuf.len, len);
				inbuf.pos = 0;
				inbuf.len += len;
				if (!(msg_bytes -= len) && !ibuf[inbuf.len-1])
					inbuf.len--, add_null = 1;
				if (iconvbufs(ic_send, &inbuf, &outbuf, flags) < 0) {
					if (errno == E2BIG)
						goto overflow;
					/* Buffer ended with an incomplete char, so move the
					 * bytes to the start of the buffer and continue. */
					memmove(ibuf, ibuf + inbuf.pos, inbuf.len);
				}
				flags &= ~ICB_INIT;
			}
			if (add_null) {
				if (outbuf.len == outbuf.size)
					goto overflow;
				outbuf.buf[outbuf.len++] = '\0';
			}
			msg_bytes = outbuf.len;
		} else
#endif
			raw_read_buf(data, msg_bytes);
		iobuf.in_multiplexed = 1;
		/* A directory name was sent with the trailing null */
		if (msg_bytes > 0 && !data[msg_bytes-1])
			log_delete(data, S_IFDIR);
		else {
			data[msg_bytes] = '\0';
			log_delete(data, S_IFREG);
		}
		break;
	case MSG_SUCCESS:
		if (msg_bytes != 4) {
		  invalid_msg:
			rprintf(FERROR, "invalid multi-message %d:%lu [%s%s]\n",
				tag, (unsigned long)msg_bytes, who_am_i(),
				inc_recurse ? "/inc" : "");
			exit_cleanup(RERR_STREAMIO);
		}
		val = raw_read_int();
		iobuf.in_multiplexed = 1;
		if (am_generator)
			got_flist_entry_status(FES_SUCCESS, val);
		else
			successful_send(val);
		break;
	case MSG_NO_SEND:
		if (msg_bytes != 4)
			goto invalid_msg;
		val = raw_read_int();
		iobuf.in_multiplexed = 1;
		if (am_generator)
			got_flist_entry_status(FES_NO_SEND, val);
		else
			send_msg_int(MSG_NO_SEND, val);
		break;
	case MSG_ERROR_SOCKET:
	case MSG_ERROR_UTF8:
	case MSG_CLIENT:
	case MSG_LOG:
		if (!am_generator)
			goto invalid_msg;
		if (tag == MSG_ERROR_SOCKET)
			msgs2stderr = 1;
		/* FALL THROUGH */
	case MSG_INFO:
	case MSG_ERROR:
	case MSG_ERROR_XFER:
	case MSG_WARNING:
		if (msg_bytes >= sizeof data) {
		    overflow:
			rprintf(FERROR,
				"multiplexing overflow %d:%lu [%s%s]\n",
				tag, (unsigned long)msg_bytes, who_am_i(),
				inc_recurse ? "/inc" : "");
			exit_cleanup(RERR_STREAMIO);
		}
		raw_read_buf(data, msg_bytes);
		/* We don't set in_multiplexed value back to 1 before writing this message
		 * because the write might loop back and read yet another message, over and
		 * over again, while waiting for room to put the message in the msg buffer. */
		rwrite((enum logcode)tag, data, msg_bytes, !am_generator);
		iobuf.in_multiplexed = 1;
		if (first_message) {
			if (list_only && !am_sender && tag == 1 && msg_bytes < sizeof data) {
				data[msg_bytes] = '\0';
				check_for_d_option_error(data);
			}
			first_message = 0;
		}
		break;
	case MSG_ERROR_EXIT:
		if (msg_bytes == 4)
			val = raw_read_int();
		else if (msg_bytes == 0)
			val = 0;
		else
			goto invalid_msg;
		iobuf.in_multiplexed = 1;
		if (DEBUG_GTE(EXIT, 3))
			rprintf(FINFO, "[%s] got MSG_ERROR_EXIT with %ld bytes\n", who_am_i(), (long)msg_bytes);
		if (msg_bytes == 0) {
			if (!am_sender && !am_generator) {
				if (DEBUG_GTE(EXIT, 3)) {
					rprintf(FINFO, "[%s] sending MSG_ERROR_EXIT (len 0)\n",
						who_am_i());
				}
				send_msg(MSG_ERROR_EXIT, "", 0, 0);
				io_flush(FULL_FLUSH);
			}
		} else if (protocol_version >= 31) {
			if (am_generator || am_receiver) {
				if (DEBUG_GTE(EXIT, 3)) {
					rprintf(FINFO, "[%s] sending MSG_ERROR_EXIT with exit_code %d\n",
						who_am_i(), val);
				}
				send_msg_int(MSG_ERROR_EXIT, val);
			} else {
				if (DEBUG_GTE(EXIT, 3)) {
					rprintf(FINFO, "[%s] sending MSG_ERROR_EXIT (len 0)\n",
						who_am_i());
				}
				send_msg(MSG_ERROR_EXIT, "", 0, 0);
			}
		}
		/* Send a negative linenum so that we don't end up
		 * with a duplicate exit message. */
		_exit_cleanup(val, __FILE__, 0 - __LINE__);
	default:
		rprintf(FERROR, "unexpected tag %d [%s%s]\n",
			tag, who_am_i(), inc_recurse ? "/inc" : "");
		exit_cleanup(RERR_STREAMIO);
	}

	assert(iobuf.in_multiplexed > 0);
}

static void drain_multiplex_messages(void)
{
	while (IN_MULTIPLEXED_AND_READY && iobuf.in.len) {
		if (iobuf.raw_input_ends_before) {
			size_t raw_len = iobuf.raw_input_ends_before - iobuf.in.pos;
			iobuf.raw_input_ends_before = 0;
			if (raw_len >= iobuf.in.len) {
				iobuf.in.len = 0;
				break;
			}
			iobuf.in.len -= raw_len;
			if ((iobuf.in.pos += raw_len) >= iobuf.in.size)
				iobuf.in.pos -= iobuf.in.size;
		}
		read_a_msg();
	}
}

void wait_for_receiver(void)
{
	if (!iobuf.raw_input_ends_before)
		read_a_msg();

	if (iobuf.raw_input_ends_before) {
		int ndx = read_int(iobuf.in_fd);
		if (ndx < 0) {
			switch (ndx) {
			case NDX_FLIST_EOF:
				flist_eof = 1;
				if (DEBUG_GTE(FLIST, 3))
					rprintf(FINFO, "[%s] flist_eof=1\n", who_am_i());
				break;
			case NDX_DONE:
				msgdone_cnt++;
				break;
			default:
				exit_cleanup(RERR_STREAMIO);
			}
		} else {
			struct file_list *flist;
			flist_receiving_enabled = False;
			if (DEBUG_GTE(FLIST, 2)) {
				rprintf(FINFO, "[%s] receiving flist for dir %d\n",
					who_am_i(), ndx);
			}
			flist = recv_file_list(iobuf.in_fd, ndx);
			flist->parent_ndx = ndx;
#ifdef SUPPORT_HARD_LINKS
			if (preserve_hard_links)
				match_hard_links(flist);
#endif
			flist_receiving_enabled = True;
		}
	}
}

unsigned short read_shortint(int f)
{
	char b[2];
	read_buf(f, b, 2);
	return (UVAL(b, 1) << 8) + UVAL(b, 0);
}

int32 read_int(int f)
{
	char b[4];
	int32 num;

	read_buf(f, b, 4);
	num = IVAL(b, 0);
#if SIZEOF_INT32 > 4
	if (num & (int32)0x80000000)
		num |= ~(int32)0xffffffff;
#endif
	return num;
}

int32 read_varint(int f)
{
	union {
		char b[5];
		int32 x;
	} u;
	uchar ch;
	int extra;

	u.x = 0;
	ch = read_byte(f);
	extra = int_byte_extra[ch / 4];
	if (extra) {
		uchar bit = ((uchar)1<<(8-extra));
		if (extra >= (int)sizeof u.b) {
			rprintf(FERROR, "Overflow in read_varint()\n");
			exit_cleanup(RERR_STREAMIO);
		}
		read_buf(f, u.b, extra);
		u.b[extra] = ch & (bit-1);
	} else
		u.b[0] = ch;
#if CAREFUL_ALIGNMENT
	u.x = IVAL(u.b,0);
#endif
#if SIZEOF_INT32 > 4
	if (u.x & (int32)0x80000000)
		u.x |= ~(int32)0xffffffff;
#endif
	return u.x;
}

int64 read_varlong(int f, uchar min_bytes)
{
	union {
		char b[9];
		int64 x;
	} u;
	char b2[8];
	int extra;

#if SIZEOF_INT64 < 8
	memset(u.b, 0, 8);
#else
	u.x = 0;
#endif
	read_buf(f, b2, min_bytes);
	memcpy(u.b, b2+1, min_bytes-1);
	extra = int_byte_extra[CVAL(b2, 0) / 4];
	if (extra) {
		uchar bit = ((uchar)1<<(8-extra));
		if (min_bytes + extra > (int)sizeof u.b) {
			rprintf(FERROR, "Overflow in read_varlong()\n");
			exit_cleanup(RERR_STREAMIO);
		}
		read_buf(f, u.b + min_bytes - 1, extra);
		u.b[min_bytes + extra - 1] = CVAL(b2, 0) & (bit-1);
#if SIZEOF_INT64 < 8
		if (min_bytes + extra > 5 || u.b[4] || CVAL(u.b,3) & 0x80) {
			rprintf(FERROR, "Integer overflow: attempted 64-bit offset\n");
			exit_cleanup(RERR_UNSUPPORTED);
		}
#endif
	} else
		u.b[min_bytes + extra - 1] = CVAL(b2, 0);
#if SIZEOF_INT64 < 8
	u.x = IVAL(u.b,0);
#elif CAREFUL_ALIGNMENT
	u.x = IVAL64(u.b,0);
#endif
	return u.x;
}

int64 read_longint(int f)
{
#if SIZEOF_INT64 >= 8
	char b[9];
#endif
	int32 num = read_int(f);

	if (num != (int32)0xffffffff)
		return num;

#if SIZEOF_INT64 < 8
	rprintf(FERROR, "Integer overflow: attempted 64-bit offset\n");
	exit_cleanup(RERR_UNSUPPORTED);
#else
	read_buf(f, b, 8);
	return IVAL(b,0) | (((int64)IVAL(b,4))<<32);
#endif
}

void read_buf(int f, char *buf, size_t len)
{
	if (f != iobuf.in_fd) {
		if (safe_read(f, buf, len) != len)
			whine_about_eof(False); /* Doesn't return. */
		goto batch_copy;
	}

	if (!IN_MULTIPLEXED) {
		raw_read_buf(buf, len);
		total_data_read += len;
		if (forward_flist_data)
			write_buf(iobuf.out_fd, buf, len);
	  batch_copy:
		if (f == write_batch_monitor_in)
			safe_write(batch_fd, buf, len);
		return;
	}

	while (1) {
		size_t siz;

		while (!iobuf.raw_input_ends_before)
			read_a_msg();

		siz = MIN(len, iobuf.raw_input_ends_before - iobuf.in.pos);
		if (siz >= iobuf.in.size)
			siz = iobuf.in.size;
		raw_read_buf(buf, siz);
		total_data_read += siz;

		if (forward_flist_data)
			write_buf(iobuf.out_fd, buf, siz);

		if (f == write_batch_monitor_in)
			safe_write(batch_fd, buf, siz);

		if ((len -= siz) == 0)
			break;
		buf += siz;
	}
}

void read_sbuf(int f, char *buf, size_t len)
{
	read_buf(f, buf, len);
	buf[len] = '\0';
}

uchar read_byte(int f)
{
	uchar c;
	read_buf(f, (char*)&c, 1);
	return c;
}

int read_vstring(int f, char *buf, int bufsize)
{
	int len = read_byte(f);

	if (len & 0x80)
		len = (len & ~0x80) * 0x100 + read_byte(f);

	if (len >= bufsize) {
		rprintf(FERROR, "over-long vstring received (%d > %d)\n",
			len, bufsize - 1);
		return -1;
	}

	if (len)
		read_buf(f, buf, len);
	buf[len] = '\0';
	return len;
}

/* Populate a sum_struct with values from the socket.  This is
 * called by both the sender and the receiver. */
void read_sum_head(int f, struct sum_struct *sum)
{
	int32 max_blength = protocol_version < 30 ? OLD_MAX_BLOCK_SIZE : MAX_BLOCK_SIZE;
	sum->count = read_int(f);
	if (sum->count < 0) {
		rprintf(FERROR, "Invalid checksum count %ld [%s]\n",
			(long)sum->count, who_am_i());
		exit_cleanup(RERR_PROTOCOL);
	}
	sum->blength = read_int(f);
	if (sum->blength < 0 || sum->blength > max_blength) {
		rprintf(FERROR, "Invalid block length %ld [%s]\n",
			(long)sum->blength, who_am_i());
		exit_cleanup(RERR_PROTOCOL);
	}
	sum->s2length = protocol_version < 27 ? csum_length : (int)read_int(f);
	if (sum->s2length < 0 || sum->s2length > MAX_DIGEST_LEN) {
		rprintf(FERROR, "Invalid checksum length %d [%s]\n",
			sum->s2length, who_am_i());
		exit_cleanup(RERR_PROTOCOL);
	}
	sum->remainder = read_int(f);
	if (sum->remainder < 0 || sum->remainder > sum->blength) {
		rprintf(FERROR, "Invalid remainder length %ld [%s]\n",
			(long)sum->remainder, who_am_i());
		exit_cleanup(RERR_PROTOCOL);
	}
}

/* Send the values from a sum_struct over the socket.  Set sum to
 * NULL if there are no checksums to send.  This is called by both
 * the generator and the sender. */
void write_sum_head(int f, struct sum_struct *sum)
{
	static struct sum_struct null_sum;

	if (sum == NULL)
		sum = &null_sum;

	write_int(f, sum->count);
	write_int(f, sum->blength);
	if (protocol_version >= 27)
		write_int(f, sum->s2length);
	write_int(f, sum->remainder);
}

/* Sleep after writing to limit I/O bandwidth usage.
 *
 * @todo Rather than sleeping after each write, it might be better to
 * use some kind of averaging.  The current algorithm seems to always
 * use a bit less bandwidth than specified, because it doesn't make up
 * for slow periods.  But arguably this is a feature.  In addition, we
 * ought to take the time used to write the data into account.
 *
 * During some phases of big transfers (file FOO is uptodate) this is
 * called with a small bytes_written every time.  As the kernel has to
 * round small waits up to guarantee that we actually wait at least the
 * requested number of microseconds, this can become grossly inaccurate.
 * We therefore keep track of the bytes we've written over time and only
 * sleep when the accumulated delay is at least 1 tenth of a second. */
static void sleep_for_bwlimit(int bytes_written)
{
	static struct timeval prior_tv;
	static long total_written = 0;
	struct timeval tv, start_tv;
	long elapsed_usec, sleep_usec;

#define ONE_SEC	1000000L /* # of microseconds in a second */

	total_written += bytes_written;

	gettimeofday(&start_tv, NULL);
	if (prior_tv.tv_sec) {
		elapsed_usec = (start_tv.tv_sec - prior_tv.tv_sec) * ONE_SEC
			     + (start_tv.tv_usec - prior_tv.tv_usec);
		total_written -= (int64)elapsed_usec * bwlimit / (ONE_SEC/1024);
		if (total_written < 0)
			total_written = 0;
	}

	sleep_usec = total_written * (ONE_SEC/1024) / bwlimit;
	if (sleep_usec < ONE_SEC / 10) {
		prior_tv = start_tv;
		return;
	}

	tv.tv_sec  = sleep_usec / ONE_SEC;
	tv.tv_usec = sleep_usec % ONE_SEC;
	select(0, NULL, NULL, NULL, &tv);

	gettimeofday(&prior_tv, NULL);
	elapsed_usec = (prior_tv.tv_sec - start_tv.tv_sec) * ONE_SEC
		     + (prior_tv.tv_usec - start_tv.tv_usec);
	total_written = (sleep_usec - elapsed_usec) * bwlimit / (ONE_SEC/1024);
}

void io_flush(int flush_it_all)
{
	if (iobuf.out.len > iobuf.out_empty_len) {
		if (flush_it_all) /* FULL_FLUSH: flush everything in the output buffers */
			perform_io(iobuf.out.size - iobuf.out_empty_len, PIO_NEED_OUTROOM);
		else /* NORMAL_FLUSH: flush at least 1 byte */
			perform_io(iobuf.out.size - iobuf.out.len + 1, PIO_NEED_OUTROOM);
	}
	if (iobuf.msg.len)
		perform_io(iobuf.msg.size, PIO_NEED_MSGROOM);
}

void write_shortint(int f, unsigned short x)
{
	char b[2];
	b[0] = (char)x;
	b[1] = (char)(x >> 8);
	write_buf(f, b, 2);
}

void write_int(int f, int32 x)
{
	char b[4];
	SIVAL(b, 0, x);
	write_buf(f, b, 4);
}

void write_varint(int f, int32 x)
{
	char b[5];
	uchar bit;
	int cnt = 4;

	SIVAL(b, 1, x);

	while (cnt > 1 && b[cnt] == 0)
		cnt--;
	bit = ((uchar)1<<(7-cnt+1));
	if (CVAL(b, cnt) >= bit) {
		cnt++;
		*b = ~(bit-1);
	} else if (cnt > 1)
		*b = b[cnt] | ~(bit*2-1);
	else
		*b = b[cnt];

	write_buf(f, b, cnt);
}

void write_varlong(int f, int64 x, uchar min_bytes)
{
	char b[9];
	uchar bit;
	int cnt = 8;

#if SIZEOF_INT64 >= 8
	SIVAL64(b, 1, x);
#else
	SIVAL(b, 1, x);
	if (x <= 0x7FFFFFFF && x >= 0)
		memset(b + 5, 0, 4);
	else {
		rprintf(FERROR, "Integer overflow: attempted 64-bit offset\n");
		exit_cleanup(RERR_UNSUPPORTED);
	}
#endif

	while (cnt > min_bytes && b[cnt] == 0)
		cnt--;
	bit = ((uchar)1<<(7-cnt+min_bytes));
	if (CVAL(b, cnt) >= bit) {
		cnt++;
		*b = ~(bit-1);
	} else if (cnt > min_bytes)
		*b = b[cnt] | ~(bit*2-1);
	else
		*b = b[cnt];

	write_buf(f, b, cnt);
}

/*
 * Note: int64 may actually be a 32-bit type if ./configure couldn't find any
 * 64-bit types on this platform.
 */
void write_longint(int f, int64 x)
{
	char b[12], * const s = b+4;

	SIVAL(s, 0, x);
	if (x <= 0x7FFFFFFF && x >= 0) {
		write_buf(f, s, 4);
		return;
	}

#if SIZEOF_INT64 < 8
	rprintf(FERROR, "Integer overflow: attempted 64-bit offset\n");
	exit_cleanup(RERR_UNSUPPORTED);
#else
	memset(b, 0xFF, 4);
	SIVAL(s, 4, x >> 32);
	write_buf(f, b, 12);
#endif
}

void write_bigbuf(int f, const char *buf, size_t len)
{
	size_t half_max = (iobuf.out.size - iobuf.out_empty_len) / 2;

	while (len > half_max + 1024) {
		write_buf(f, buf, half_max);
		buf += half_max;
		len -= half_max;
	}

	write_buf(f, buf, len);
}

void write_buf(int f, const char *buf, size_t len)
{
	size_t pos, siz;

	if (f != iobuf.out_fd) {
		safe_write(f, buf, len);
		goto batch_copy;
	}

	if (iobuf.out.len + len > iobuf.out.size)
		perform_io(len, PIO_NEED_OUTROOM);

	pos = iobuf.out.pos + iobuf.out.len; /* Must be set after any flushing. */
	if (pos >= iobuf.out.size)
		pos -= iobuf.out.size;

	/* Handle a split copy if we wrap around the end of the circular buffer. */
	if (pos >= iobuf.out.pos && (siz = iobuf.out.size - pos) < len) {
		memcpy(iobuf.out.buf + pos, buf, siz);
		memcpy(iobuf.out.buf, buf + siz, len - siz);
	} else
		memcpy(iobuf.out.buf + pos, buf, len);

	iobuf.out.len += len;
	total_data_written += len;

  batch_copy:
	if (f == write_batch_monitor_out)
		safe_write(batch_fd, buf, len);
}

/* Write a string to the connection */
void write_sbuf(int f, const char *buf)
{
	write_buf(f, buf, strlen(buf));
}

void write_byte(int f, uchar c)
{
	write_buf(f, (char *)&c, 1);
}

void write_vstring(int f, const char *str, int len)
{
	uchar lenbuf[3], *lb = lenbuf;

	if (len > 0x7F) {
		if (len > 0x7FFF) {
			rprintf(FERROR,
				"attempting to send over-long vstring (%d > %d)\n",
				len, 0x7FFF);
			exit_cleanup(RERR_PROTOCOL);
		}
		*lb++ = len / 0x100 + 0x80;
	}
	*lb = len;

	write_buf(f, (char*)lenbuf, lb - lenbuf + 1);
	if (len)
		write_buf(f, str, len);
}

/* Send a file-list index using a byte-reduction method. */
void write_ndx(int f, int32 ndx)
{
	static int32 prev_positive = -1, prev_negative = 1;
	int32 diff, cnt = 0;
	char b[6];

	if (protocol_version < 30 || read_batch) {
		write_int(f, ndx);
		return;
	}

	/* Send NDX_DONE as a single-byte 0 with no side effects.  Send
	 * negative nums as a positive after sending a leading 0xFF. */
	if (ndx >= 0) {
		diff = ndx - prev_positive;
		prev_positive = ndx;
	} else if (ndx == NDX_DONE) {
		*b = 0;
		write_buf(f, b, 1);
		return;
	} else {
		b[cnt++] = (char)0xFF;
		ndx = -ndx;
		diff = ndx - prev_negative;
		prev_negative = ndx;
	}

	/* A diff of 1 - 253 is sent as a one-byte diff; a diff of 254 - 32767
	 * or 0 is sent as a 0xFE + a two-byte diff; otherwise we send 0xFE
	 * & all 4 bytes of the (non-negative) num with the high-bit set. */
	if (diff < 0xFE && diff > 0)
		b[cnt++] = (char)diff;
	else if (diff < 0 || diff > 0x7FFF) {
		b[cnt++] = (char)0xFE;
		b[cnt++] = (char)((ndx >> 24) | 0x80);
		b[cnt++] = (char)ndx;
		b[cnt++] = (char)(ndx >> 8);
		b[cnt++] = (char)(ndx >> 16);
	} else {
		b[cnt++] = (char)0xFE;
		b[cnt++] = (char)(diff >> 8);
		b[cnt++] = (char)diff;
	}
	write_buf(f, b, cnt);
}

/* Receive a file-list index using a byte-reduction method. */
int32 read_ndx(int f)
{
	static int32 prev_positive = -1, prev_negative = 1;
	int32 *prev_ptr, num;
	char b[4];

	if (protocol_version < 30)
		return read_int(f);

	read_buf(f, b, 1);
	if (CVAL(b, 0) == 0xFF) {
		read_buf(f, b, 1);
		prev_ptr = &prev_negative;
	} else if (CVAL(b, 0) == 0)
		return NDX_DONE;
	else
		prev_ptr = &prev_positive;
	if (CVAL(b, 0) == 0xFE) {
		read_buf(f, b, 2);
		if (CVAL(b, 0) & 0x80) {
			b[3] = CVAL(b, 0) & ~0x80;
			b[0] = b[1];
			read_buf(f, b+1, 2);
			num = IVAL(b, 0);
		} else
			num = (UVAL(b,0)<<8) + UVAL(b,1) + *prev_ptr;
	} else
		num = UVAL(b, 0) + *prev_ptr;
	*prev_ptr = num;
	if (prev_ptr == &prev_negative)
		num = -num;
	return num;
}

/* Read a line of up to bufsiz-1 characters into buf.  Strips
 * the (required) trailing newline and all carriage returns.
 * Returns 1 for success; 0 for I/O error or truncation. */
int read_line_old(int fd, char *buf, size_t bufsiz, int eof_ok)
{
	assert(fd != iobuf.in_fd);
	bufsiz--; /* leave room for the null */
	while (bufsiz > 0) {
		if (safe_read(fd, buf, 1) == 0) {
			if (eof_ok)
				break;
			return 0;
		}
		if (*buf == '\0')
			return 0;
		if (*buf == '\n')
			break;
		if (*buf != '\r') {
			buf++;
			bufsiz--;
		}
	}
	*buf = '\0';
	return bufsiz > 0;
}

void io_printf(int fd, const char *format, ...)
{
	va_list ap;
	char buf[BIGPATHBUFLEN];
	int len;

	va_start(ap, format);
	len = vsnprintf(buf, sizeof buf, format, ap);
	va_end(ap);

	if (len < 0)
		exit_cleanup(RERR_PROTOCOL);

	if (len >= (int)sizeof buf) {
		rprintf(FERROR, "io_printf() was too long for the buffer.\n");
		exit_cleanup(RERR_PROTOCOL);
	}

	write_sbuf(fd, buf);
}

/* Setup for multiplexing a MSG_* stream with the data stream. */
void io_start_multiplex_out(int fd)
{
	io_flush(FULL_FLUSH);

	if (msgs2stderr && DEBUG_GTE(IO, 2))
		rprintf(FINFO, "[%s] io_start_multiplex_out(%d)\n", who_am_i(), fd);

	if (!iobuf.msg.buf)
		alloc_xbuf(&iobuf.msg, ROUND_UP_1024(IO_BUFFER_SIZE));

	iobuf.out_empty_len = 4; /* See also OUT_MULTIPLEXED */
	io_start_buffering_out(fd);
	got_kill_signal = 0;

	iobuf.raw_data_header_pos = iobuf.out.pos + iobuf.out.len;
	iobuf.out.len += 4;
}

/* Setup for multiplexing a MSG_* stream with the data stream. */
void io_start_multiplex_in(int fd)
{
	if (msgs2stderr && DEBUG_GTE(IO, 2))
		rprintf(FINFO, "[%s] io_start_multiplex_in(%d)\n", who_am_i(), fd);

	iobuf.in_multiplexed = 1; /* See also IN_MULTIPLEXED */
	io_start_buffering_in(fd);
}

int io_end_multiplex_in(int mode)
{
	int ret = iobuf.in_multiplexed ? iobuf.in_fd : -1;

	if (msgs2stderr && DEBUG_GTE(IO, 2))
		rprintf(FINFO, "[%s] io_end_multiplex_in(mode=%d)\n", who_am_i(), mode);

	iobuf.in_multiplexed = 0;
	if (mode == MPLX_SWITCHING)
		iobuf.raw_input_ends_before = 0;
	else
		assert(iobuf.raw_input_ends_before == 0);
	if (mode != MPLX_TO_BUFFERED)
		io_end_buffering_in(mode);

	return ret;
}

int io_end_multiplex_out(int mode)
{
	int ret = iobuf.out_empty_len ? iobuf.out_fd : -1;

	if (msgs2stderr && DEBUG_GTE(IO, 2))
		rprintf(FINFO, "[%s] io_end_multiplex_out(mode=%d)\n", who_am_i(), mode);

	if (mode != MPLX_TO_BUFFERED)
		io_end_buffering_out(mode);
	else
		io_flush(FULL_FLUSH);

	iobuf.out.len = 0;
	iobuf.out_empty_len = 0;
	if (got_kill_signal > 0) /* Just in case... */
		handle_kill_signal(False);
	got_kill_signal = -1;

	return ret;
}

void start_write_batch(int fd)
{
	/* Some communication has already taken place, but we don't
	 * enable batch writing until here so that we can write a
	 * canonical record of the communication even though the
	 * actual communication so far depends on whether a daemon
	 * is involved. */
	write_int(batch_fd, protocol_version);
	if (protocol_version >= 30)
		write_byte(batch_fd, compat_flags);
	write_int(batch_fd, checksum_seed);

	if (am_sender)
		write_batch_monitor_out = fd;
	else
		write_batch_monitor_in = fd;
}

void stop_write_batch(void)
{
	write_batch_monitor_out = -1;
	write_batch_monitor_in = -1;
}

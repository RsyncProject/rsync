/*
 * Socket and pipe I/O utilities used in rsync.
 *
 * Copyright (C) 1996-2001 Andrew Tridgell
 * Copyright (C) 1996 Paul Mackerras
 * Copyright (C) 2001, 2002 Martin Pool <mbp@samba.org>
 * Copyright (C) 2003-2009 Wayne Davison
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

/** If no timeout is specified then use a 60 second select timeout */
#define SELECT_TIMEOUT 60

extern int bwlimit;
extern size_t bwlimit_writemax;
extern int io_timeout;
extern int allowed_lull;
extern int am_server;
extern int am_daemon;
extern int am_sender;
extern int am_generator;
extern int inc_recurse;
extern int io_error;
extern int eol_nulls;
extern int flist_eof;
extern int list_only;
extern int read_batch;
extern int csum_length;
extern int protect_args;
extern int checksum_seed;
extern int protocol_version;
extern int remove_source_files;
extern int preserve_hard_links;
extern struct stats stats;
extern struct file_list *cur_flist;
#ifdef ICONV_OPTION
extern int filesfrom_convert;
extern iconv_t ic_send, ic_recv;
#endif

const char phase_unknown[] = "unknown";
int ignore_timeout = 0;
int batch_fd = -1;
int msgdone_cnt = 0;

/* Ignore an EOF error if non-zero. See whine_about_eof(). */
int kluge_around_eof = 0;

int msg_fd_in = -1;
int msg_fd_out = -1;
int sock_f_in = -1;
int sock_f_out = -1;

static int iobuf_f_in = -1;
static char *iobuf_in;
static size_t iobuf_in_siz;
static size_t iobuf_in_ndx;
static size_t iobuf_in_remaining;

static int iobuf_f_out = -1;
static char *iobuf_out;
static int iobuf_out_cnt;

int flist_forward_from = -1;

static int io_multiplexing_out;
static int io_multiplexing_in;
static time_t last_io_in;
static time_t last_io_out;
static int no_flush;

static int write_batch_monitor_in = -1;
static int write_batch_monitor_out = -1;

static int io_filesfrom_f_in = -1;
static int io_filesfrom_f_out = -1;
static xbuf ff_buf = EMPTY_XBUF;
static char ff_lastchar;
#ifdef ICONV_OPTION
static xbuf iconv_buf = EMPTY_XBUF;
#endif
static int defer_forwarding_messages = 0, keep_defer_forwarding = 0;
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

#define REMOTE_OPTION_ERROR "rsync: on remote machine: -"
#define REMOTE_OPTION_ERROR2 ": unknown option"

enum festatus { FES_SUCCESS, FES_REDO, FES_NO_SEND };

static void check_timeout(void)
{
	time_t t, chk;

	if (!io_timeout || ignore_timeout)
		return;

	t = time(NULL);

	if (!last_io_in)
		last_io_in = t;

	chk = MAX(last_io_out, last_io_in);
	if (t - chk >= io_timeout) {
		if (am_server || am_daemon)
			exit_cleanup(RERR_TIMEOUT);
		rprintf(FERROR, "[%s] io timeout after %d seconds -- exiting\n",
			who_am_i(), (int)(t-chk));
		exit_cleanup(RERR_TIMEOUT);
	}
}

static void readfd(int fd, char *buffer, size_t N);
static void writefd(int fd, const char *buf, size_t len);
static void writefd_unbuffered(int fd, const char *buf, size_t len);
static void mplex_write(int fd, enum msgcode code, const char *buf, size_t len, int convert);

static flist_ndx_list redo_list, hlink_list;

struct msg_list_item {
	struct msg_list_item *next;
	char convert;
	char buf[1];
};

struct msg_list {
	struct msg_list_item *head, *tail;
};

static struct msg_list msg_queue;

static void got_flist_entry_status(enum festatus status, const char *buf)
{
	int ndx = IVAL(buf, 0);
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
			send_msg(MSG_SUCCESS, buf, 4, 0);
		if (preserve_hard_links) {
			struct file_struct *file = flist->files[ndx - flist->ndx_start];
			if (F_IS_HLINKED(file)) {
				flist_ndx_push(&hlink_list, ndx);
				flist->in_progress++;
			}
		}
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
	case FES_NO_SEND:
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

/* Setup the fd used to receive MSG_* messages.  Only needed during the
 * early stages of being a local sender (up through the sending of the
 * file list) or when we're the generator (to fetch the messages from
 * the receiver). */
void set_msg_fd_in(int fd)
{
	msg_fd_in = fd;
}

/* Setup the fd used to send our MSG_* messages.  Only needed when
 * we're the receiver (to send our messages to the generator). */
void set_msg_fd_out(int fd)
{
	msg_fd_out = fd;
	set_nonblocking(msg_fd_out);
}

/* Add a message to the pending MSG_* list. */
static void msg_list_add(struct msg_list *lst, int code, const char *buf, int len, int convert)
{
	struct msg_list_item *m;
	int sz = len + 4 + sizeof m[0] - 1;

	if (!(m = (struct msg_list_item *)new_array(char, sz)))
		out_of_memory("msg_list_add");
	m->next = NULL;
	m->convert = convert;
	SIVAL(m->buf, 0, ((code+MPLEX_BASE)<<24) | len);
	memcpy(m->buf + 4, buf, len);
	if (lst->tail)
		lst->tail->next = m;
	else
		lst->head = m;
	lst->tail = m;
}

static inline int flush_a_msg(int fd)
{
	struct msg_list_item *m = msg_queue.head;
	int len = IVAL(m->buf, 0) & 0xFFFFFF;
	int tag = *((uchar*)m->buf+3) - MPLEX_BASE;

	if (!(msg_queue.head = m->next))
		msg_queue.tail = NULL;

	defer_forwarding_messages++;
	mplex_write(fd, tag, m->buf + 4, len, m->convert);
	defer_forwarding_messages--;

	free(m);

	return len;
}

static void msg_flush(void)
{
	if (am_generator) {
		while (msg_queue.head && io_multiplexing_out)
			stats.total_written += flush_a_msg(sock_f_out) + 4;
	} else {
		while (msg_queue.head)
			(void)flush_a_msg(msg_fd_out);
	}
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

/* Read a message from the MSG_* fd and handle it.  This is called either
 * during the early stages of being a local sender (up through the sending
 * of the file list) or when we're the generator (to fetch the messages
 * from the receiver). */
static void read_msg_fd(void)
{
	char buf[2048];
	size_t n;
	struct file_list *flist;
	int fd = msg_fd_in;
	int tag, len;

	/* Temporarily disable msg_fd_in.  This is needed to avoid looping back
	 * to this routine from writefd_unbuffered(). */
	no_flush++;
	msg_fd_in = -1;
	defer_forwarding_messages++;

	readfd(fd, buf, 4);
	tag = IVAL(buf, 0);

	len = tag & 0xFFFFFF;
	tag = (tag >> 24) - MPLEX_BASE;

	switch (tag) {
	case MSG_DONE:
		if (len < 0 || len > 1 || !am_generator) {
		  invalid_msg:
			rprintf(FERROR, "invalid message %d:%d [%s%s]\n",
				tag, len, who_am_i(),
				inc_recurse ? "/inc" : "");
			exit_cleanup(RERR_STREAMIO);
		}
		if (len) {
			readfd(fd, buf, len);
			stats.total_read = read_varlong(fd, 3);
		}
		msgdone_cnt++;
		break;
	case MSG_REDO:
		if (len != 4 || !am_generator)
			goto invalid_msg;
		readfd(fd, buf, 4);
		got_flist_entry_status(FES_REDO, buf);
		break;
	case MSG_FLIST:
		if (len != 4 || !am_generator || !inc_recurse)
			goto invalid_msg;
		readfd(fd, buf, 4);
		/* Read extra file list from receiver. */
		assert(iobuf_in != NULL);
		assert(iobuf_f_in == fd);
		if (verbose > 3) {
			rprintf(FINFO, "[%s] receiving flist for dir %d\n",
				who_am_i(), IVAL(buf,0));
		}
		flist = recv_file_list(fd);
		flist->parent_ndx = IVAL(buf,0);
#ifdef SUPPORT_HARD_LINKS
		if (preserve_hard_links)
			match_hard_links(flist);
#endif
		break;
	case MSG_FLIST_EOF:
		if (len != 0 || !am_generator || !inc_recurse)
			goto invalid_msg;
		flist_eof = 1;
		break;
	case MSG_IO_ERROR:
		if (len != 4)
			goto invalid_msg;
		readfd(fd, buf, len);
		io_error |= IVAL(buf, 0);
		break;
	case MSG_DELETED:
		if (len >= (int)sizeof buf || !am_generator)
			goto invalid_msg;
		readfd(fd, buf, len);
		send_msg(MSG_DELETED, buf, len, 1);
		break;
	case MSG_SUCCESS:
		if (len != 4 || !am_generator)
			goto invalid_msg;
		readfd(fd, buf, 4);
		got_flist_entry_status(FES_SUCCESS, buf);
		break;
	case MSG_NO_SEND:
		if (len != 4 || !am_generator)
			goto invalid_msg;
		readfd(fd, buf, 4);
		got_flist_entry_status(FES_NO_SEND, buf);
		break;
	case MSG_ERROR_SOCKET:
	case MSG_ERROR_UTF8:
	case MSG_CLIENT:
		if (!am_generator)
			goto invalid_msg;
		if (tag == MSG_ERROR_SOCKET)
			io_end_multiplex_out();
		/* FALL THROUGH */
	case MSG_INFO:
	case MSG_ERROR:
	case MSG_ERROR_XFER:
	case MSG_WARNING:
	case MSG_LOG:
		while (len) {
			n = len;
			if (n >= sizeof buf)
				n = sizeof buf - 1;
			readfd(fd, buf, n);
			rwrite((enum logcode)tag, buf, n, !am_generator);
			len -= n;
		}
		break;
	default:
		rprintf(FERROR, "unknown message %d:%d [%s]\n",
			tag, len, who_am_i());
		exit_cleanup(RERR_STREAMIO);
	}

	no_flush--;
	msg_fd_in = fd;
	if (!--defer_forwarding_messages && !no_flush)
		msg_flush();
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
		if (iobuf_out_cnt)
			io_flush(NORMAL_FLUSH);
		else
			read_msg_fd();
	}

	active_filecnt++;
	active_bytecnt += F_LENGTH(cur_flist->files[ndx - cur_flist->ndx_start]);
}

/* Write an message to a multiplexed stream. If this fails, rsync exits. */
static void mplex_write(int fd, enum msgcode code, const char *buf, size_t len, int convert)
{
	char buffer[BIGPATHBUFLEN]; /* Oversized for use by iconv code. */
	size_t n = len;

#ifdef ICONV_OPTION
	/* We need to convert buf before doing anything else so that we
	 * can include the (converted) byte length in the message header. */
	if (convert && ic_send != (iconv_t)-1) {
		xbuf outbuf, inbuf;

		INIT_XBUF(outbuf, buffer + 4, 0, sizeof buffer - 4);
		INIT_XBUF(inbuf, (char*)buf, len, -1);

		iconvbufs(ic_send, &inbuf, &outbuf,
			  ICB_INCLUDE_BAD | ICB_INCLUDE_INCOMPLETE);
		if (inbuf.len > 0) {
			rprintf(FERROR, "overflowed conversion buffer in mplex_write");
			exit_cleanup(RERR_UNSUPPORTED);
		}

		n = len = outbuf.len;
	} else
#endif
	if (n > 1024 - 4) /* BIGPATHBUFLEN can handle 1024 bytes */
		n = 0;    /* We'd rather do 2 writes than too much memcpy(). */
	else
		memcpy(buffer + 4, buf, n);

	SIVAL(buffer, 0, ((MPLEX_BASE + (int)code)<<24) + len);

	keep_defer_forwarding++; /* defer_forwarding_messages++ on return */
	writefd_unbuffered(fd, buffer, n+4);
	keep_defer_forwarding--;

	if (len > n)
		writefd_unbuffered(fd, buf+n, len-n);

	if (!--defer_forwarding_messages && !no_flush)
		msg_flush();
}

int send_msg(enum msgcode code, const char *buf, int len, int convert)
{
	if (msg_fd_out < 0) {
		if (!defer_forwarding_messages)
			return io_multiplex_write(code, buf, len, convert);
		if (!io_multiplexing_out)
			return 0;
		msg_list_add(&msg_queue, code, buf, len, convert);
		return 1;
	}
	if (flist_forward_from >= 0)
		msg_list_add(&msg_queue, code, buf, len, convert);
	else
		mplex_write(msg_fd_out, code, buf, len, convert);
	return 1;
}

void send_msg_int(enum msgcode code, int num)
{
	char numbuf[4];
	SIVAL(numbuf, 0, num);
	send_msg(code, numbuf, 4, 0);
}

void wait_for_receiver(void)
{
	if (io_flush(NORMAL_FLUSH))
		return;
	read_msg_fd();
}

int get_redo_num(void)
{
	return flist_ndx_pop(&redo_list);
}

int get_hlink_num(void)
{
	return flist_ndx_pop(&hlink_list);
}

/**
 * When we're the receiver and we have a local --files-from list of names
 * that needs to be sent over the socket to the sender, we have to do two
 * things at the same time: send the sender a list of what files we're
 * processing and read the incoming file+info list from the sender.  We do
 * this by augmenting the read_timeout() function to copy this data.  It
 * uses ff_buf to read a block of data from f_in (when it is ready, since
 * it might be a pipe) and then blast it out f_out (when it is ready to
 * receive more data).
 */
void io_set_filesfrom_fds(int f_in, int f_out)
{
	io_filesfrom_f_in = f_in;
	io_filesfrom_f_out = f_out;
	alloc_xbuf(&ff_buf, 2048);
#ifdef ICONV_OPTION
	if (protect_args)
		alloc_xbuf(&iconv_buf, 1024);
#endif
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
static void whine_about_eof(int fd)
{
	if (kluge_around_eof && fd == sock_f_in) {
		int i;
		if (kluge_around_eof > 0)
			exit_cleanup(0);
		/* If we're still here after 10 seconds, exit with an error. */
		for (i = 10*1000/20; i--; )
			msleep(20);
	}

	rprintf(FERROR, RSYNC_NAME ": connection unexpectedly closed "
		"(%.0f bytes received so far) [%s]\n",
		(double)stats.total_read, who_am_i());

	exit_cleanup(RERR_STREAMIO);
}

/**
 * Read from a socket with I/O timeout. return the number of bytes
 * read. If no bytes can be read then exit, never return a number <= 0.
 *
 * TODO: If the remote shell connection fails, then current versions
 * actually report an "unexpected EOF" error here.  Since it's a
 * fairly common mistake to try to use rsh when ssh is required, we
 * should trap that: if we fail to read any data at all, we should
 * give a better explanation.  We can tell whether the connection has
 * started by looking e.g. at whether the remote version is known yet.
 */
static int read_timeout(int fd, char *buf, size_t len)
{
	int n, cnt = 0;

	io_flush(FULL_FLUSH);

	while (cnt == 0) {
		/* until we manage to read *something* */
		fd_set r_fds, w_fds;
		struct timeval tv;
		int maxfd = fd;
		int count;

		FD_ZERO(&r_fds);
		FD_ZERO(&w_fds);
		FD_SET(fd, &r_fds);
		if (io_filesfrom_f_out >= 0) {
			int new_fd;
			if (ff_buf.len == 0) {
				if (io_filesfrom_f_in >= 0) {
					FD_SET(io_filesfrom_f_in, &r_fds);
					new_fd = io_filesfrom_f_in;
				} else {
					io_filesfrom_f_out = -1;
					new_fd = -1;
				}
			} else {
				FD_SET(io_filesfrom_f_out, &w_fds);
				new_fd = io_filesfrom_f_out;
			}
			if (new_fd > maxfd)
				maxfd = new_fd;
		}

		tv.tv_sec = select_timeout;
		tv.tv_usec = 0;

		errno = 0;

		count = select(maxfd + 1, &r_fds, &w_fds, NULL, &tv);

		if (count <= 0) {
			if (errno == EBADF) {
				defer_forwarding_messages = 0;
				exit_cleanup(RERR_SOCKETIO);
			}
			check_timeout();
			continue;
		}

		if (io_filesfrom_f_out >= 0) {
			if (ff_buf.len) {
				if (FD_ISSET(io_filesfrom_f_out, &w_fds)) {
					int l = write(io_filesfrom_f_out,
						      ff_buf.buf + ff_buf.pos,
						      ff_buf.len);
					if (l > 0) {
						if (!(ff_buf.len -= l))
							ff_buf.pos = 0;
						else
							ff_buf.pos += l;
					} else if (errno != EINTR) {
						/* XXX should we complain? */
						io_filesfrom_f_out = -1;
					}
				}
			} else if (io_filesfrom_f_in >= 0) {
				if (FD_ISSET(io_filesfrom_f_in, &r_fds)) {
#ifdef ICONV_OPTION
					xbuf *ibuf = filesfrom_convert ? &iconv_buf : &ff_buf;
#else
					xbuf *ibuf = &ff_buf;
#endif
					int l = read(io_filesfrom_f_in, ibuf->buf, ibuf->size);
					if (l <= 0) {
						if (l == 0 || errno != EINTR) {
							/* Send end-of-file marker */
							memcpy(ff_buf.buf, "\0\0", 2);
							ff_buf.len = ff_lastchar? 2 : 1;
							ff_buf.pos = 0;
							io_filesfrom_f_in = -1;
						}
					} else {
#ifdef ICONV_OPTION
						if (filesfrom_convert) {
							iconv_buf.pos = 0;
							iconv_buf.len = l;
							iconvbufs(ic_send, &iconv_buf, &ff_buf,
							    ICB_EXPAND_OUT|ICB_INCLUDE_BAD|ICB_INCLUDE_INCOMPLETE);
							l = ff_buf.len;
						}
#endif
						if (!eol_nulls) {
							char *s = ff_buf.buf + l;
							/* Transform CR and/or LF into '\0' */
							while (s-- > ff_buf.buf) {
								if (*s == '\n' || *s == '\r')
									*s = '\0';
							}
						}
						if (!ff_lastchar) {
							/* Last buf ended with a '\0', so don't
							 * let this buf start with one. */
							while (l && ff_buf.buf[ff_buf.pos] == '\0')
								ff_buf.pos++, l--;
						}
						if (!l)
							ff_buf.pos = 0;
						else {
							char *f = ff_buf.buf + ff_buf.pos;
							char *t = f;
							char *eob = f + l;
							/* Eliminate any multi-'\0' runs. */
							while (f != eob) {
								if (!(*t++ = *f++)) {
									while (f != eob && !*f)
										f++, l--;
								}
							}
							ff_lastchar = f[-1];
						}
						ff_buf.len = l;
					}
				}
			}
		}

		if (!FD_ISSET(fd, &r_fds))
			continue;

		n = read(fd, buf, len);

		if (n <= 0) {
			if (n == 0)
				whine_about_eof(fd); /* Doesn't return. */
			if (errno == EINTR || errno == EWOULDBLOCK
			    || errno == EAGAIN)
				continue;

			/* Don't write errors on a dead socket. */
			if (fd == sock_f_in) {
				io_end_multiplex_out();
				rsyserr(FERROR_SOCKET, errno, "read error");
			} else
				rsyserr(FERROR, errno, "read error");
			exit_cleanup(RERR_STREAMIO);
		}

		buf += n;
		len -= n;
		cnt += n;

		if (fd == sock_f_in && io_timeout)
			last_io_in = time(NULL);
	}

	return cnt;
}

/* Read a line into the "buf" buffer. */
int read_line(int fd, char *buf, size_t bufsiz, int flags)
{
	char ch, *s, *eob;
	int cnt;

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
		cnt = read(fd, &ch, 1);
		if (cnt < 0 && (errno == EWOULDBLOCK
		  || errno == EINTR || errno == EAGAIN)) {
			struct timeval tv;
			fd_set r_fds, e_fds;
			FD_ZERO(&r_fds);
			FD_SET(fd, &r_fds);
			FD_ZERO(&e_fds);
			FD_SET(fd, &e_fds);
			tv.tv_sec = select_timeout;
			tv.tv_usec = 0;
			if (!select(fd+1, &r_fds, NULL, &e_fds, &tv))
				check_timeout();
			/*if (FD_ISSET(fd, &e_fds))
				rprintf(FINFO, "select exception on fd %d\n", fd); */
			continue;
		}
		if (cnt != 1)
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
			  ICB_INCLUDE_BAD | ICB_INCLUDE_INCOMPLETE);
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
	int dot_pos = 0;
	int argc = 0;
	char **argv, *p;
	int rl_flags = (rl_nulls ? RL_EOL_NULLS : 0);

#ifdef ICONV_OPTION
	rl_flags |= (protect_args && ic_recv != (iconv_t)-1 ? RL_CONVERT : 0);
#endif

	if (!(argv = new_array(char *, maxargs)))
		out_of_memory("read_args");
	if (mod_name && !protect_args)
		argv[argc++] = "rsyncd";

	while (1) {
		if (read_line(f_in, buf, bufsiz, rl_flags) == 0)
			break;

		if (argc == maxargs-1) {
			maxargs += MAX_ARGS;
			if (!(argv = realloc_array(argv, char *, maxargs)))
				out_of_memory("read_args");
		}

		if (dot_pos) {
			if (request_p) {
				*request_p = strdup(buf);
				request_p = NULL;
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

int io_start_buffering_out(int f_out)
{
	if (iobuf_out) {
		assert(f_out == iobuf_f_out);
		return 0;
	}
	if (!(iobuf_out = new_array(char, IO_BUFFER_SIZE)))
		out_of_memory("io_start_buffering_out");
	iobuf_out_cnt = 0;
	iobuf_f_out = f_out;
	return 1;
}

int io_start_buffering_in(int f_in)
{
	if (iobuf_in) {
		assert(f_in == iobuf_f_in);
		return 0;
	}
	iobuf_in_siz = 2 * IO_BUFFER_SIZE;
	if (!(iobuf_in = new_array(char, iobuf_in_siz)))
		out_of_memory("io_start_buffering_in");
	iobuf_f_in = f_in;
	return 1;
}

void io_end_buffering_in(void)
{
	if (!iobuf_in)
		return;
	free(iobuf_in);
	iobuf_in = NULL;
	iobuf_in_ndx = 0;
	iobuf_in_remaining = 0;
	iobuf_f_in = -1;
}

void io_end_buffering_out(void)
{
	if (!iobuf_out)
		return;
	io_flush(FULL_FLUSH);
	free(iobuf_out);
	iobuf_out = NULL;
	iobuf_f_out = -1;
}

void maybe_flush_socket(int important)
{
	if (iobuf_out && iobuf_out_cnt
	 && (important || time(NULL) - last_io_out >= 5))
		io_flush(NORMAL_FLUSH);
}

void maybe_send_keepalive(void)
{
	if (time(NULL) - last_io_out >= allowed_lull) {
		if (!iobuf_out || !iobuf_out_cnt) {
			if (protocol_version < 29)
				send_msg(MSG_DATA, "", 0, 0);
			else if (protocol_version >= 30)
				send_msg(MSG_NOOP, "", 0, 0);
			else {
				write_int(sock_f_out, cur_flist->used);
				write_shortint(sock_f_out, ITEM_IS_NEW);
			}
		}
		if (iobuf_out)
			io_flush(NORMAL_FLUSH);
	}
}

void start_flist_forward(int f_in)
{
	assert(iobuf_out != NULL);
	assert(iobuf_f_out == msg_fd_out);
	flist_forward_from = f_in;
	defer_forwarding_messages++;
}

void stop_flist_forward(void)
{
	flist_forward_from = -1;
	defer_forwarding_messages--;
	io_flush(FULL_FLUSH);
}

/**
 * Continue trying to read len bytes - don't return until len has been
 * read.
 **/
static void read_loop(int fd, char *buf, size_t len)
{
	while (len) {
		int n = read_timeout(fd, buf, len);

		buf += n;
		len -= n;
	}
}

/**
 * Read from the file descriptor handling multiplexing - return number
 * of bytes read.
 *
 * Never returns <= 0.
 */
static int readfd_unbuffered(int fd, char *buf, size_t len)
{
	size_t msg_bytes;
	int tag, cnt = 0;
	char line[BIGPATHBUFLEN];

	if (!iobuf_in || fd != iobuf_f_in)
		return read_timeout(fd, buf, len);

	if (!io_multiplexing_in && iobuf_in_remaining == 0) {
		iobuf_in_remaining = read_timeout(fd, iobuf_in, iobuf_in_siz);
		iobuf_in_ndx = 0;
	}

	while (cnt == 0) {
		if (iobuf_in_remaining) {
			len = MIN(len, iobuf_in_remaining);
			memcpy(buf, iobuf_in + iobuf_in_ndx, len);
			iobuf_in_ndx += len;
			iobuf_in_remaining -= len;
			cnt = len;
			break;
		}

		read_loop(fd, line, 4);
		tag = IVAL(line, 0);

		msg_bytes = tag & 0xFFFFFF;
		tag = (tag >> 24) - MPLEX_BASE;

		switch (tag) {
		case MSG_DATA:
			if (msg_bytes > iobuf_in_siz) {
				if (!(iobuf_in = realloc_array(iobuf_in, char,
							       msg_bytes)))
					out_of_memory("readfd_unbuffered");
				iobuf_in_siz = msg_bytes;
			}
			read_loop(fd, iobuf_in, msg_bytes);
			iobuf_in_remaining = msg_bytes;
			iobuf_in_ndx = 0;
			break;
		case MSG_NOOP:
			if (msg_bytes != 0)
				goto invalid_msg;
			if (am_sender)
				maybe_send_keepalive();
			break;
		case MSG_IO_ERROR:
			if (msg_bytes != 4)
				goto invalid_msg;
			read_loop(fd, line, msg_bytes);
			send_msg_int(MSG_IO_ERROR, IVAL(line, 0));
			io_error |= IVAL(line, 0);
			break;
		case MSG_DELETED:
			if (msg_bytes >= sizeof line)
				goto overflow;
#ifdef ICONV_OPTION
			if (ic_recv != (iconv_t)-1) {
				xbuf outbuf, inbuf;
				char ibuf[512];
				int add_null = 0;
				int pos = 0;

				INIT_CONST_XBUF(outbuf, line);
				INIT_XBUF(inbuf, ibuf, 0, -1);

				while (msg_bytes) {
					inbuf.len = msg_bytes > sizeof ibuf
						  ? sizeof ibuf : msg_bytes;
					read_loop(fd, inbuf.buf, inbuf.len);
					if (!(msg_bytes -= inbuf.len)
					 && !ibuf[inbuf.len-1])
						inbuf.len--, add_null = 1;
					if (iconvbufs(ic_send, &inbuf, &outbuf,
					    ICB_INCLUDE_BAD | ICB_INCLUDE_INCOMPLETE) < 0)
						goto overflow;
					pos = -1;
				}
				if (add_null) {
					if (outbuf.len == outbuf.size)
						goto overflow;
					outbuf.buf[outbuf.len++] = '\0';
				}
				msg_bytes = outbuf.len;
			} else
#endif
				read_loop(fd, line, msg_bytes);
			/* A directory name was sent with the trailing null */
			if (msg_bytes > 0 && !line[msg_bytes-1])
				log_delete(line, S_IFDIR);
			else {
				line[msg_bytes] = '\0';
				log_delete(line, S_IFREG);
			}
			break;
		case MSG_SUCCESS:
			if (msg_bytes != 4) {
			  invalid_msg:
				rprintf(FERROR, "invalid multi-message %d:%ld [%s]\n",
					tag, (long)msg_bytes, who_am_i());
				exit_cleanup(RERR_STREAMIO);
			}
			read_loop(fd, line, msg_bytes);
			successful_send(IVAL(line, 0));
			break;
		case MSG_NO_SEND:
			if (msg_bytes != 4)
				goto invalid_msg;
			read_loop(fd, line, msg_bytes);
			send_msg_int(MSG_NO_SEND, IVAL(line, 0));
			break;
		case MSG_INFO:
		case MSG_ERROR:
		case MSG_ERROR_XFER:
		case MSG_WARNING:
			if (msg_bytes >= sizeof line) {
			    overflow:
				rprintf(FERROR,
					"multiplexing overflow %d:%ld [%s]\n",
					tag, (long)msg_bytes, who_am_i());
				exit_cleanup(RERR_STREAMIO);
			}
			read_loop(fd, line, msg_bytes);
			rwrite((enum logcode)tag, line, msg_bytes, 1);
			if (first_message) {
				if (list_only && !am_sender && tag == 1) {
					line[msg_bytes] = '\0';
					check_for_d_option_error(line);
				}
				first_message = 0;
			}
			break;
		default:
			rprintf(FERROR, "unexpected tag %d [%s]\n",
				tag, who_am_i());
			exit_cleanup(RERR_STREAMIO);
		}
	}

	if (iobuf_in_remaining == 0)
		io_flush(NORMAL_FLUSH);

	return cnt;
}

/* Do a buffered read from fd.  Don't return until all N bytes have
 * been read.  If all N can't be read then exit with an error. */
static void readfd(int fd, char *buffer, size_t N)
{
	int  cnt;
	size_t total = 0;

	while (total < N) {
		cnt = readfd_unbuffered(fd, buffer + total, N-total);
		total += cnt;
	}

	if (fd == write_batch_monitor_in) {
		if ((size_t)write(batch_fd, buffer, total) != total)
			exit_cleanup(RERR_FILEIO);
	}

	if (fd == flist_forward_from)
		writefd(iobuf_f_out, buffer, total);

	if (fd == sock_f_in)
		stats.total_read += total;
}

unsigned short read_shortint(int f)
{
	char b[2];
	readfd(f, b, 2);
	return (UVAL(b, 1) << 8) + UVAL(b, 0);
}

int32 read_int(int f)
{
	char b[4];
	int32 num;

	readfd(f, b, 4);
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
	readfd(f, (char*)&ch, 1);
	extra = int_byte_extra[ch / 4];
	if (extra) {
		uchar bit = ((uchar)1<<(8-extra));
		if (extra >= (int)sizeof u.b) {
			rprintf(FERROR, "Overflow in read_varint()\n");
			exit_cleanup(RERR_STREAMIO);
		}
		readfd(f, u.b, extra);
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
	readfd(f, b2, min_bytes);
	memcpy(u.b, b2+1, min_bytes-1);
	extra = int_byte_extra[CVAL(b2, 0) / 4];
	if (extra) {
		uchar bit = ((uchar)1<<(8-extra));
		if (min_bytes + extra > (int)sizeof u.b) {
			rprintf(FERROR, "Overflow in read_varlong()\n");
			exit_cleanup(RERR_STREAMIO);
		}
		readfd(f, u.b + min_bytes - 1, extra);
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
	u.x = IVAL(u.b,0) | (((int64)IVAL(u.b,4))<<32);
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
	readfd(f, b, 8);
	return IVAL(b,0) | (((int64)IVAL(b,4))<<32);
#endif
}

void read_buf(int f, char *buf, size_t len)
{
	readfd(f,buf,len);
}

void read_sbuf(int f, char *buf, size_t len)
{
	readfd(f, buf, len);
	buf[len] = '\0';
}

uchar read_byte(int f)
{
	uchar c;
	readfd(f, (char *)&c, 1);
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
		readfd(f, buf, len);
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

/**
 * Sleep after writing to limit I/O bandwidth usage.
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
 * sleep when the accumulated delay is at least 1 tenth of a second.
 **/
static void sleep_for_bwlimit(int bytes_written)
{
	static struct timeval prior_tv;
	static long total_written = 0;
	struct timeval tv, start_tv;
	long elapsed_usec, sleep_usec;

#define ONE_SEC	1000000L /* # of microseconds in a second */

	if (!bwlimit_writemax)
		return;

	total_written += bytes_written;

	gettimeofday(&start_tv, NULL);
	if (prior_tv.tv_sec) {
		elapsed_usec = (start_tv.tv_sec - prior_tv.tv_sec) * ONE_SEC
			     + (start_tv.tv_usec - prior_tv.tv_usec);
		total_written -= elapsed_usec * bwlimit / (ONE_SEC/1024);
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

static const char *what_fd_is(int fd)
{
	static char buf[20];

	if (fd == sock_f_out)
		return "socket";
	else if (fd == msg_fd_out)
		return "message fd";
	else if (fd == batch_fd)
		return "batch file";
	else {
		snprintf(buf, sizeof buf, "fd %d", fd);
		return buf;
	}
}

/* Write len bytes to the file descriptor fd, looping as necessary to get
 * the job done and also (in certain circumstances) reading any data on
 * msg_fd_in to avoid deadlock.
 *
 * This function underlies the multiplexing system.  The body of the
 * application never calls this function directly. */
static void writefd_unbuffered(int fd, const char *buf, size_t len)
{
	size_t n, total = 0;
	fd_set w_fds, r_fds, e_fds;
	int maxfd, count, cnt, using_r_fds;
	int defer_inc = 0;
	struct timeval tv;

	if (no_flush++)
		defer_forwarding_messages++, defer_inc++;

	while (total < len) {
		FD_ZERO(&w_fds);
		FD_SET(fd, &w_fds);
		FD_ZERO(&e_fds);
		FD_SET(fd, &e_fds);
		maxfd = fd;

		if (msg_fd_in >= 0) {
			FD_ZERO(&r_fds);
			FD_SET(msg_fd_in, &r_fds);
			if (msg_fd_in > maxfd)
				maxfd = msg_fd_in;
			using_r_fds = 1;
		} else
			using_r_fds = 0;

		tv.tv_sec = select_timeout;
		tv.tv_usec = 0;

		errno = 0;
		count = select(maxfd + 1, using_r_fds ? &r_fds : NULL,
			       &w_fds, &e_fds, &tv);

		if (count <= 0) {
			if (count < 0 && errno == EBADF)
				exit_cleanup(RERR_SOCKETIO);
			check_timeout();
			continue;
		}

		/*if (FD_ISSET(fd, &e_fds))
			rprintf(FINFO, "select exception on fd %d\n", fd); */

		if (using_r_fds && FD_ISSET(msg_fd_in, &r_fds))
			read_msg_fd();

		if (!FD_ISSET(fd, &w_fds))
			continue;

		n = len - total;
		if (bwlimit_writemax && n > bwlimit_writemax)
			n = bwlimit_writemax;
		cnt = write(fd, buf + total, n);

		if (cnt <= 0) {
			if (cnt < 0) {
				if (errno == EINTR)
					continue;
				if (errno == EWOULDBLOCK || errno == EAGAIN) {
					msleep(1);
					continue;
				}
			}

			/* Don't try to write errors back across the stream. */
			if (fd == sock_f_out)
				io_end_multiplex_out();
			/* Don't try to write errors down a failing msg pipe. */
			if (am_server && fd == msg_fd_out)
				exit_cleanup(RERR_STREAMIO);
			rsyserr(FERROR, errno,
				"writefd_unbuffered failed to write %ld bytes to %s [%s]",
				(long)len, what_fd_is(fd), who_am_i());
			/* If the other side is sending us error messages, try
			 * to grab any messages they sent before they died. */
			while (!am_server && fd == sock_f_out && io_multiplexing_in) {
				char buf[1024];
				set_io_timeout(30);
				ignore_timeout = 0;
				readfd_unbuffered(sock_f_in, buf, sizeof buf);
			}
			exit_cleanup(RERR_STREAMIO);
		}

		total += cnt;
		defer_forwarding_messages++, defer_inc++;

		if (fd == sock_f_out) {
			if (io_timeout || am_generator)
				last_io_out = time(NULL);
			sleep_for_bwlimit(cnt);
		}
	}

	no_flush--;
	if (keep_defer_forwarding)
		defer_inc--;
	if (!(defer_forwarding_messages -= defer_inc) && !no_flush)
		msg_flush();
}

int io_flush(int flush_it_all)
{
	int flushed_something = 0;

	if (no_flush)
		return 0;

	if (iobuf_out_cnt) {
		if (io_multiplexing_out)
			mplex_write(sock_f_out, MSG_DATA, iobuf_out, iobuf_out_cnt, 0);
		else
			writefd_unbuffered(iobuf_f_out, iobuf_out, iobuf_out_cnt);
		iobuf_out_cnt = 0;
		flushed_something = 1;
	}

	if (flush_it_all && !defer_forwarding_messages && msg_queue.head) {
		msg_flush();
		flushed_something = 1;
	}

	return flushed_something;
}

static void writefd(int fd, const char *buf, size_t len)
{
	if (fd == sock_f_out)
		stats.total_written += len;

	if (fd == write_batch_monitor_out)
		writefd_unbuffered(batch_fd, buf, len);

	if (!iobuf_out || fd != iobuf_f_out) {
		writefd_unbuffered(fd, buf, len);
		return;
	}

	while (len) {
		int n = MIN((int)len, IO_BUFFER_SIZE - iobuf_out_cnt);
		if (n > 0) {
			memcpy(iobuf_out+iobuf_out_cnt, buf, n);
			buf += n;
			len -= n;
			iobuf_out_cnt += n;
		}

		if (iobuf_out_cnt == IO_BUFFER_SIZE)
			io_flush(NORMAL_FLUSH);
	}
}

void write_shortint(int f, unsigned short x)
{
	char b[2];
	b[0] = (char)x;
	b[1] = (char)(x >> 8);
	writefd(f, b, 2);
}

void write_int(int f, int32 x)
{
	char b[4];
	SIVAL(b, 0, x);
	writefd(f, b, 4);
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

	writefd(f, b, cnt);
}

void write_varlong(int f, int64 x, uchar min_bytes)
{
	char b[9];
	uchar bit;
	int cnt = 8;

	SIVAL(b, 1, x);
#if SIZEOF_INT64 >= 8
	SIVAL(b, 5, x >> 32);
#else
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

	writefd(f, b, cnt);
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
		writefd(f, s, 4);
		return;
	}

#if SIZEOF_INT64 < 8
	rprintf(FERROR, "Integer overflow: attempted 64-bit offset\n");
	exit_cleanup(RERR_UNSUPPORTED);
#else
	memset(b, 0xFF, 4);
	SIVAL(s, 4, x >> 32);
	writefd(f, b, 12);
#endif
}

void write_buf(int f, const char *buf, size_t len)
{
	writefd(f,buf,len);
}

/** Write a string to the connection */
void write_sbuf(int f, const char *buf)
{
	writefd(f, buf, strlen(buf));
}

void write_byte(int f, uchar c)
{
	writefd(f, (char *)&c, 1);
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

	writefd(f, (char*)lenbuf, lb - lenbuf + 1);
	if (len)
		writefd(f, str, len);
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
		writefd(f, b, 1);
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
	writefd(f, b, cnt);
}

/* Receive a file-list index using a byte-reduction method. */
int32 read_ndx(int f)
{
	static int32 prev_positive = -1, prev_negative = 1;
	int32 *prev_ptr, num;
	char b[4];

	if (protocol_version < 30)
		return read_int(f);

	readfd(f, b, 1);
	if (CVAL(b, 0) == 0xFF) {
		readfd(f, b, 1);
		prev_ptr = &prev_negative;
	} else if (CVAL(b, 0) == 0)
		return NDX_DONE;
	else
		prev_ptr = &prev_positive;
	if (CVAL(b, 0) == 0xFE) {
		readfd(f, b, 2);
		if (CVAL(b, 0) & 0x80) {
			b[3] = CVAL(b, 0) & ~0x80;
			b[0] = b[1];
			readfd(f, b+1, 2);
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
int read_line_old(int f, char *buf, size_t bufsiz)
{
	bufsiz--; /* leave room for the null */
	while (bufsiz > 0) {
		buf[0] = 0;
		read_buf(f, buf, 1);
		if (buf[0] == 0)
			return 0;
		if (buf[0] == '\n')
			break;
		if (buf[0] != '\r') {
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
		exit_cleanup(RERR_STREAMIO);

	if (len > (int)sizeof buf) {
		rprintf(FERROR, "io_printf() was too long for the buffer.\n");
		exit_cleanup(RERR_STREAMIO);
	}

	write_sbuf(fd, buf);
}

/** Setup for multiplexing a MSG_* stream with the data stream. */
void io_start_multiplex_out(void)
{
	io_flush(NORMAL_FLUSH);
	io_start_buffering_out(sock_f_out);
	io_multiplexing_out = 1;
}

/** Setup for multiplexing a MSG_* stream with the data stream. */
void io_start_multiplex_in(void)
{
	io_flush(NORMAL_FLUSH);
	io_start_buffering_in(sock_f_in);
	io_multiplexing_in = 1;
}

/** Write an message to the multiplexed data stream. */
int io_multiplex_write(enum msgcode code, const char *buf, size_t len, int convert)
{
	if (!io_multiplexing_out)
		return 0;
	io_flush(NORMAL_FLUSH);
	stats.total_written += (len+4);
	mplex_write(sock_f_out, code, buf, len, convert);
	return 1;
}

void io_end_multiplex_in(void)
{
	io_multiplexing_in = 0;
	io_end_buffering_in();
}

/** Stop output multiplexing. */
void io_end_multiplex_out(void)
{
	io_multiplexing_out = 0;
	io_end_buffering_out();
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
		write_byte(batch_fd, inc_recurse);
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

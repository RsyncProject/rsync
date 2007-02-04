/*
 * Socket and pipe I/O utilities used in rsync.
 *
 * Copyright (C) 1996-2001 Andrew Tridgell
 * Copyright (C) 1996 Paul Mackerras
 * Copyright (C) 2001, 2002 Martin Pool <mbp@samba.org>
 * Copyright (C) 2003-2007 Wayne Davison
 *
 * This program is free software; you can redistribute it and/or modify
 * it under the terms of the GNU General Public License version 2 as
 * published by the Free Software Foundation.
 *
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License along
 * with this program; if not, write to the Free Software Foundation, Inc.,
 * 51 Franklin Street - Fifth Floor, Boston, MA 02110-1301, USA.
 */

/* Rsync provides its own multiplexing system, which is used to send
 * stderr and stdout over a single socket.
 *
 * For historical reasons this is off during the start of the
 * connection, but it's switched on quite early using
 * io_start_multiplex_out() and io_start_multiplex_in(). */

#include "rsync.h"

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
extern int read_batch;
extern int csum_length;
extern int checksum_seed;
extern int protocol_version;
extern int remove_source_files;
extern int preserve_hard_links;
extern char *filesfrom_host;
extern struct stats stats;
extern struct file_list *cur_flist, *first_flist;

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
static char io_filesfrom_buf[2048];
static char *io_filesfrom_bp;
static char io_filesfrom_lastchar;
static int io_filesfrom_buflen;
static int defer_forwarding_messages = 0;
static int select_timeout = SELECT_TIMEOUT;
static int active_filecnt = 0;
static OFF_T active_bytecnt = 0;

static char int_byte_cnt[64] = {
	3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, /* (00 - 3F)/4 */
	3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, /* (40 - 7F)/4 */
	4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, /* (80 - BF)/4 */
	5, 5, 5, 5, 5, 5, 5, 5, 6, 6, 6, 6, 7, 7, 8, 9, /* (C0 - FF)/4 */
};

static void readfd(int fd, char *buffer, size_t N);
static void writefd(int fd, const char *buf, size_t len);
static void writefd_unbuffered(int fd, const char *buf, size_t len);
static void decrement_active_files(int ndx);
static void decrement_flist_in_progress(int ndx, int redo);

struct flist_ndx_item {
	struct flist_ndx_item *next;
	int ndx;
};

struct flist_ndx_list {
	struct flist_ndx_item *head, *tail;
};

static struct flist_ndx_list redo_list, hlink_list;

struct msg_list_item {
	struct msg_list_item *next;
	int len;
	char buf[1];
};

struct msg_list {
	struct msg_list_item *head, *tail;
};

static struct msg_list msg2sndr;

static void flist_ndx_push(struct flist_ndx_list *lp, int ndx)
{
	struct flist_ndx_item *item;

	if (!(item = new(struct flist_ndx_item)))
		out_of_memory("flist_ndx_push");
	item->next = NULL;
	item->ndx = ndx;
	if (lp->tail)
		lp->tail->next = item;
	else
		lp->head = item;
	lp->tail = item;
}

static int flist_ndx_pop(struct flist_ndx_list *lp)
{
	struct flist_ndx_item *next;
	int ndx;

	if (!lp->head)
		return -1;

	ndx = lp->head->ndx;
	next = lp->head->next;
	free(lp->head);
	lp->head = next;
	if (!next)
		lp->tail = NULL;

	return ndx;
}

static void check_timeout(void)
{
	time_t t;

	if (!io_timeout || ignore_timeout)
		return;

	if (!last_io_in) {
		last_io_in = time(NULL);
		return;
	}

	t = time(NULL);

	if (t - last_io_in >= io_timeout) {
		if (!am_server && !am_daemon) {
			rprintf(FERROR, "io timeout after %d seconds -- exiting\n",
				(int)(t-last_io_in));
		}
		exit_cleanup(RERR_TIMEOUT);
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

	if (!io_timeout || io_timeout > SELECT_TIMEOUT)
		select_timeout = SELECT_TIMEOUT;
	else
		select_timeout = io_timeout;

	allowed_lull = read_batch ? 0 : (io_timeout + 1) / 2;
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
static void msg_list_add(struct msg_list *lst, int code, const char *buf, int len)
{
	struct msg_list_item *m;
	int sz = len + 4 + sizeof m[0] - 1;

	if (!(m = (struct msg_list_item *)new_array(char, sz)))
		out_of_memory("msg_list_add");
	m->next = NULL;
	m->len = len + 4;
	SIVAL(m->buf, 0, ((code+MPLEX_BASE)<<24) | len);
	memcpy(m->buf + 4, buf, len);
	if (lst->tail)
		lst->tail->next = m;
	else
		lst->head = m;
	lst->tail = m;
}

static void msg2sndr_flush(void)
{
	while (msg2sndr.head && io_multiplexing_out) {
		struct msg_list_item *m = msg2sndr.head;
		if (!(msg2sndr.head = m->next))
			msg2sndr.tail = NULL;
		stats.total_written += m->len;
		defer_forwarding_messages = 1;
		writefd_unbuffered(sock_f_out, m->buf, m->len);
		defer_forwarding_messages = 0;
		free(m);
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
			stats.total_read = read_longint(fd);
		}
		msgdone_cnt++;
		break;
	case MSG_REDO:
		if (len != 4 || !am_generator)
			goto invalid_msg;
		readfd(fd, buf, 4);
		if (remove_source_files)
			decrement_active_files(IVAL(buf,0));
		flist_ndx_push(&redo_list, IVAL(buf,0));
		if (inc_recurse)
			decrement_flist_in_progress(IVAL(buf,0), 1);
		break;
	case MSG_FLIST:
		if (len != 4 || !am_generator || !inc_recurse)
			goto invalid_msg;
		readfd(fd, buf, 4);
		/* Read extra file list from receiver. */
		assert(iobuf_in != NULL);
		assert(iobuf_f_in == fd);
		flist = recv_file_list(fd);
		flist->parent_ndx = IVAL(buf,0);
		break;
	case MSG_FLIST_EOF:
		if (len != 0 || !am_generator || !inc_recurse)
			goto invalid_msg;
		flist_eof = 1;
		break;
	case MSG_DELETED:
		if (len >= (int)sizeof buf || !am_generator)
			goto invalid_msg;
		readfd(fd, buf, len);
		send_msg(MSG_DELETED, buf, len);
		break;
	case MSG_SUCCESS:
		if (len != 4 || !am_generator)
			goto invalid_msg;
		readfd(fd, buf, len);
		if (remove_source_files) {
			decrement_active_files(IVAL(buf,0));
			send_msg(MSG_SUCCESS, buf, len);
		}
		if (preserve_hard_links)
			flist_ndx_push(&hlink_list, IVAL(buf,0));
		if (inc_recurse)
			decrement_flist_in_progress(IVAL(buf,0), 0);
		break;
	case MSG_NO_SEND:
		if (len != 4 || !am_generator)
			goto invalid_msg;
		readfd(fd, buf, len);
		if (inc_recurse)
			decrement_flist_in_progress(IVAL(buf,0), 0);
		break;
	case MSG_SOCKERR:
	case MSG_CLIENT:
		if (!am_generator)
			goto invalid_msg;
		if (tag == MSG_SOCKERR)
			io_end_multiplex_out();
		/* FALL THROUGH */
	case MSG_INFO:
	case MSG_ERROR:
	case MSG_LOG:
		while (len) {
			n = len;
			if (n >= sizeof buf)
				n = sizeof buf - 1;
			readfd(fd, buf, n);
			rwrite((enum logcode)tag, buf, n);
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
	if (!--defer_forwarding_messages)
		msg2sndr_flush();
}

/* This is used by the generator to limit how many file transfers can
 * be active at once when --remove-source-files is specified.  Without
 * this, sender-side deletions were mostly happening at the end. */
void increment_active_files(int ndx, int itemizing, enum logcode code)
{
	/* TODO: tune these limits? */
	while (active_filecnt >= (active_bytecnt >= 128*1024 ? 10 : 50)) {
		check_for_finished_files(itemizing, code, 0);
		if (iobuf_out_cnt)
			io_flush(NORMAL_FLUSH);
		else
			read_msg_fd();
	}

	active_filecnt++;
	active_bytecnt += F_LENGTH(cur_flist->files[ndx - cur_flist->ndx_start]);
}

static void decrement_active_files(int ndx)
{
	struct file_list *flist = flist_for_ndx(ndx);
	assert(flist != NULL);
	active_filecnt--;
	active_bytecnt -= F_LENGTH(flist->files[ndx - flist->ndx_start]);
}

static void decrement_flist_in_progress(int ndx, int redo)
{
	struct file_list *flist = cur_flist ? cur_flist : first_flist;

	while (ndx < flist->ndx_start) {
		if (flist == first_flist) {
		  invalid_ndx:
			rprintf(FERROR,
				"Invalid file index: %d (%d - %d) [%s]\n",
				ndx, first_flist->ndx_start,
				first_flist->prev->ndx_start + first_flist->prev->count - 1,
				who_am_i());
			exit_cleanup(RERR_PROTOCOL);
		}
		flist = flist->prev;
	}
	while (ndx >= flist->ndx_start + flist->count) {
		if (!(flist = flist->next))
			goto invalid_ndx;
	}

	flist->in_progress--;
	if (redo)
		flist->to_redo++;
}

/* Write an message to a multiplexed stream. If this fails, rsync exits. */
static void mplex_write(int fd, enum msgcode code, const char *buf, size_t len)
{
	char buffer[1024];
	size_t n = len;

	SIVAL(buffer, 0, ((MPLEX_BASE + (int)code)<<24) + len);

	if (n > sizeof buffer - 4)
		n = 0;
	else
		memcpy(buffer + 4, buf, n);

	writefd_unbuffered(fd, buffer, n+4);

	len -= n;
	buf += n;

	if (len) {
		defer_forwarding_messages++;
		writefd_unbuffered(fd, buf, len);
		if (!--defer_forwarding_messages)
			msg2sndr_flush();
	}
}

int send_msg(enum msgcode code, const char *buf, int len)
{
	if (msg_fd_out < 0) {
		if (!defer_forwarding_messages)
			return io_multiplex_write(code, buf, len);
		if (!io_multiplexing_out)
			return 0;
		msg_list_add(&msg2sndr, code, buf, len);
		return 1;
	}
	mplex_write(msg_fd_out, code, buf, len);
	return 1;
}

void send_msg_int(enum msgcode code, int num)
{
	char numbuf[4];
	SIVAL(numbuf, 0, num);
	send_msg(code, numbuf, 4);
}

void wait_for_receiver(void)
{
	if (iobuf_out_cnt)
		io_flush(NORMAL_FLUSH);
	else
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
 * uses the io_filesfrom_buf to read a block of data from f_in (when it is
 * ready, since it might be a pipe) and then blast it out f_out (when it
 * is ready to receive more data).
 */
void io_set_filesfrom_fds(int f_in, int f_out)
{
	io_filesfrom_f_in = f_in;
	io_filesfrom_f_out = f_out;
	io_filesfrom_bp = io_filesfrom_buf;
	io_filesfrom_lastchar = '\0';
	io_filesfrom_buflen = 0;
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
			if (io_filesfrom_buflen == 0) {
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
			if (io_filesfrom_buflen) {
				if (FD_ISSET(io_filesfrom_f_out, &w_fds)) {
					int l = write(io_filesfrom_f_out,
						      io_filesfrom_bp,
						      io_filesfrom_buflen);
					if (l > 0) {
						if (!(io_filesfrom_buflen -= l))
							io_filesfrom_bp = io_filesfrom_buf;
						else
							io_filesfrom_bp += l;
					} else {
						/* XXX should we complain? */
						io_filesfrom_f_out = -1;
					}
				}
			} else if (io_filesfrom_f_in >= 0) {
				if (FD_ISSET(io_filesfrom_f_in, &r_fds)) {
					int l = read(io_filesfrom_f_in,
						     io_filesfrom_buf,
						     sizeof io_filesfrom_buf);
					if (l <= 0) {
						/* Send end-of-file marker */
						io_filesfrom_buf[0] = '\0';
						io_filesfrom_buf[1] = '\0';
						io_filesfrom_buflen = io_filesfrom_lastchar? 2 : 1;
						io_filesfrom_f_in = -1;
					} else {
						if (!eol_nulls) {
							char *s = io_filesfrom_buf + l;
							/* Transform CR and/or LF into '\0' */
							while (s-- > io_filesfrom_buf) {
								if (*s == '\n' || *s == '\r')
									*s = '\0';
							}
						}
						if (!io_filesfrom_lastchar) {
							/* Last buf ended with a '\0', so don't
							 * let this buf start with one. */
							while (l && !*io_filesfrom_bp)
								io_filesfrom_bp++, l--;
						}
						if (!l)
							io_filesfrom_bp = io_filesfrom_buf;
						else {
							char *f = io_filesfrom_bp;
							char *t = f;
							char *eob = f + l;
							/* Eliminate any multi-'\0' runs. */
							while (f != eob) {
								if (!(*t++ = *f++)) {
									while (f != eob && !*f)
										f++, l--;
								}
							}
							io_filesfrom_lastchar = f[-1];
						}
						io_filesfrom_buflen = l;
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
				rsyserr(FSOCKERR, errno, "read error");
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

/**
 * Read a line into the "fname" buffer (which must be at least MAXPATHLEN
 * characters long).
 */
int read_filesfrom_line(int fd, char *fname)
{
	char ch, *s, *eob = fname + MAXPATHLEN - 1;
	int cnt;
	int reading_remotely = filesfrom_host != NULL;
	int nulls = eol_nulls || reading_remotely;

  start:
	s = fname;
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
			if (FD_ISSET(fd, &e_fds)) {
				rsyserr(FINFO, errno,
					"select exception on fd %d", fd);
			}
			continue;
		}
		if (cnt != 1)
			break;
		if (nulls? !ch : (ch == '\r' || ch == '\n')) {
			/* Skip empty lines if reading locally. */
			if (!reading_remotely && s == fname)
				continue;
			break;
		}
		if (s < eob)
			*s++ = ch;
	}
	*s = '\0';

	/* Dump comments. */
	if (*fname == '#' || *fname == ';')
		goto start;

	return s - fname;
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

void maybe_flush_socket(void)
{
	if (iobuf_out && iobuf_out_cnt && time(NULL) - last_io_out >= 5)
		io_flush(NORMAL_FLUSH);
}

void maybe_send_keepalive(void)
{
	if (time(NULL) - last_io_out >= allowed_lull) {
		if (!iobuf_out || !iobuf_out_cnt) {
			if (protocol_version < 29)
				return; /* there's nothing we can do */
			if (protocol_version >= 30)
				send_msg(MSG_NOOP, "", 0);
			else {
				write_int(sock_f_out, cur_flist->count);
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
}

void stop_flist_forward()
{
	flist_forward_from = -1;
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
			if (am_sender)
				maybe_send_keepalive();
			break;
		case MSG_IO_ERROR:
			if (msg_bytes != 4)
				goto invalid_msg;
			read_loop(fd, line, msg_bytes);
			io_error |= IVAL(line, 0);
			break;
		case MSG_DELETED:
			if (msg_bytes >= sizeof line)
				goto overflow;
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
			if (msg_bytes >= sizeof line) {
			    overflow:
				rprintf(FERROR,
					"multiplexing overflow %d:%ld [%s]\n",
					tag, (long)msg_bytes, who_am_i());
				exit_cleanup(RERR_STREAMIO);
			}
			read_loop(fd, line, msg_bytes);
			rwrite((enum logcode)tag, line, msg_bytes);
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

int64 read_longint(int f)
{
	int64 num;
	char b[9];

	if (protocol_version < 30) {
		num = read_int(f);

		if ((int32)num != (int32)0xffffffff)
			return num;

#if SIZEOF_INT64 < 8
		rprintf(FERROR, "Integer overflow: attempted 64-bit offset\n");
		exit_cleanup(RERR_UNSUPPORTED);
#else
		readfd(f, b, 8);
		num = IVAL(b,0) | (((int64)IVAL(b,4))<<32);
#endif
	} else {
		int cnt;
		readfd(f, b, 3);
		cnt = int_byte_cnt[CVAL(b, 0) / 4];
#if SIZEOF_INT64 < 8
		if (cnt > 5 || (cnt == 5 && (CVAL(b,0)&0x3F || CVAL(b,1)&0x80))) {
			rprintf(FERROR, "Integer overflow: attempted 64-bit offset\n");
			exit_cleanup(RERR_UNSUPPORTED);
		}
#endif
		if (cnt > 3)
			readfd(f, b + 3, cnt - 3);
		switch (cnt) {
		case 3:
			num = NVAL3(b, 0);
			break;
		case 4:
			num = NVAL4(b, 0x80);
			break;
		case 5:
			num = NVAL5(b, 0xC0);
			break;
#if SIZEOF_INT64 >= 8
		case 6:
			num = NVAL6(b, 0xE0);
			break;
		case 7:
			num = NVAL7(b, 0xF0);
			break;
		case 8:
			num = NVAL8(b, 0xF8);
			break;
		case 9:
			num = NVAL8(b+1, 0);
			break;
#endif
		default:
			exit_cleanup(RERR_PROTOCOL); /* impossible... */
		}
	}

	return num;
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
	sum->count = read_int(f);
	if (sum->count < 0) {
		rprintf(FERROR, "Invalid checksum count %ld [%s]\n",
			(long)sum->count, who_am_i());
		exit_cleanup(RERR_PROTOCOL);
	}
	sum->blength = read_int(f);
	if (sum->blength < 0 || sum->blength > MAX_BLOCK_SIZE) {
		rprintf(FERROR, "Invalid block length %ld [%s]\n",
			(long)sum->blength, who_am_i());
		exit_cleanup(RERR_PROTOCOL);
	}
	sum->s2length = protocol_version < 27 ? csum_length : (int)read_int(f);
	if (sum->s2length < 0 || sum->s2length > MD4_SUM_LENGTH) {
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
	int defer_save = defer_forwarding_messages;
	struct timeval tv;

	if (no_flush++)
		defer_forwarding_messages = 1;

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

		if (FD_ISSET(fd, &e_fds)) {
			rsyserr(FINFO, errno,
				"select exception on fd %d", fd);
		}

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
				"writefd_unbuffered failed to write %ld bytes [%s]",
				(long)len, who_am_i());
			/* If the other side is sending us error messages, try
			 * to grab any messages they sent before they died. */
			while (fd == sock_f_out && io_multiplexing_in) {
				set_io_timeout(30);
				ignore_timeout = 0;
				readfd_unbuffered(sock_f_in, io_filesfrom_buf,
						  sizeof io_filesfrom_buf);
			}
			exit_cleanup(RERR_STREAMIO);
		}

		total += cnt;
		defer_forwarding_messages = 1;

		if (fd == sock_f_out) {
			if (io_timeout || am_generator)
				last_io_out = time(NULL);
			sleep_for_bwlimit(cnt);
		}
	}

	no_flush--;
	if (!(defer_forwarding_messages = defer_save))
		msg2sndr_flush();
}

void io_flush(int flush_it_all)
{
	if (flush_it_all && !defer_forwarding_messages)
		msg2sndr_flush();

	if (!iobuf_out_cnt || no_flush)
		return;

	if (io_multiplexing_out)
		mplex_write(sock_f_out, MSG_DATA, iobuf_out, iobuf_out_cnt);
	else
		writefd_unbuffered(iobuf_f_out, iobuf_out, iobuf_out_cnt);
	iobuf_out_cnt = 0;
}

static void writefd(int fd, const char *buf, size_t len)
{
	if (fd == sock_f_out)
		stats.total_written += len;

	if (fd == write_batch_monitor_out) {
		if ((size_t)write(batch_fd, buf, len) != len)
			exit_cleanup(RERR_FILEIO);
	}

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

/*
 * Note: int64 may actually be a 32-bit type if ./configure couldn't find any
 * 64-bit types on this platform.
 */
void write_longint(int f, int64 x)
{
	char b[12];

#if SIZEOF_INT64 < 8
	if (x < 0 || x > 0x7FFFFFFF) {
		rprintf(FERROR, "Integer overflow: attempted 64-bit offset\n");
		exit_cleanup(RERR_UNSUPPORTED);
	}
#endif

	if (protocol_version < 30) {
		char * const s = b+4;
		SIVAL(s, 0, x);
#if SIZEOF_INT64 < 8
		writefd(f, s, 4);
#else
		if (x <= 0x7FFFFFFF && x >= 0) {
			writefd(f, s, 4);
			return;
		}

		memset(b, 0xFF, 4);
		SIVAL(s, 4, x >> 32);
		writefd(f, b, 12);
	} else if (x < 0) {
		goto all_bits;
#endif
	} else if (x < ((int32)1<<(3*8-1))) {
		b[0] = (char)(x >> 16);
		b[1] = (char)(x >> 8);
		b[2] = (char)x;
		writefd(f, b, 3);
	} else if (x < ((int64)1<<(4*8-2))) {
		b[0] = (char)((x >> 24) | 0x80);
		b[1] = (char)(x >> 16);
		b[2] = (char)(x >> 8);
		b[3] = (char)x;
		writefd(f, b, 4);
#if SIZEOF_INT64 < 8
	} else {
		b[0] = 0xC0;
		b[1] = (char)(x >> 24);
		b[2] = (char)(x >> 16);
		b[3] = (char)(x >> 8);
		b[4] = (char)x;
		writefd(f, b, 5);
	}
#else
	} else if (x < ((int64)1<<(5*8-3))) {
		b[0] = (char)((x >> 32) | 0xC0);
		b[1] = (char)(x >> 24);
		b[2] = (char)(x >> 16);
		b[3] = (char)(x >> 8);
		b[4] = (char)x;
		writefd(f, b, 5);
	} else if (x < ((int64)1<<(6*8-4))) {
		b[0] = (char)((x >> 40) | 0xE0);
		b[1] = (char)(x >> 32);
		b[2] = (char)(x >> 24);
		b[3] = (char)(x >> 16);
		b[4] = (char)(x >> 8);
		b[5] = (char)x;
		writefd(f, b, 6);
	} else if (x < ((int64)1<<(7*8-5))) {
		b[0] = (char)((x >> 48) | 0xF0);
		b[1] = (char)(x >> 40);
		b[2] = (char)(x >> 32);
		b[3] = (char)(x >> 24);
		b[4] = (char)(x >> 16);
		b[5] = (char)(x >> 8);
		b[6] = (char)x;
		writefd(f, b, 7);
	} else if (x < ((int64)1<<(8*8-6))) {
		b[0] = (char)((x >> 56) | 0xF8);
		b[1] = (char)(x >> 48);
		b[2] = (char)(x >> 40);
		b[3] = (char)(x >> 32);
		b[4] = (char)(x >> 24);
		b[5] = (char)(x >> 16);
		b[6] = (char)(x >> 8);
		b[7] = (char)x;
		writefd(f, b, 8);
	} else {
	  all_bits:
		b[0] = (char)0xFC;
		b[1] = (char)(x >> 56);
		b[2] = (char)(x >> 48);
		b[3] = (char)(x >> 40);
		b[4] = (char)(x >> 32);
		b[5] = (char)(x >> 24);
		b[6] = (char)(x >> 16);
		b[7] = (char)(x >> 8);
		b[8] = (char)x;
		writefd(f, b, 9);
	}
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
		b[cnt++] = (char)(ndx >> 16);
		b[cnt++] = (char)(ndx >> 8);
		b[cnt++] = (char)ndx;
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
			readfd(f, b+2, 2);
			num = NVAL4(b, 0x80);
		} else
			num = NVAL2(b, 0) + *prev_ptr;
	} else
		num = CVAL(b, 0) + *prev_ptr;
	*prev_ptr = num;
	if (prev_ptr == &prev_negative)
		num = -num;
	return num;
}

/**
 * Read a line of up to @p maxlen characters into @p buf (not counting
 * the trailing null).  Strips the (required) trailing newline and all
 * carriage returns.
 *
 * @return 1 for success; 0 for I/O error or truncation.
 **/
int read_line(int f, char *buf, size_t maxlen)
{
	while (maxlen) {
		buf[0] = 0;
		read_buf(f, buf, 1);
		if (buf[0] == 0)
			return 0;
		if (buf[0] == '\n')
			break;
		if (buf[0] != '\r') {
			buf++;
			maxlen--;
		}
	}
	*buf = '\0';
	return maxlen > 0;
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
int io_multiplex_write(enum msgcode code, const char *buf, size_t len)
{
	if (!io_multiplexing_out)
		return 0;
	io_flush(NORMAL_FLUSH);
	stats.total_written += (len+4);
	mplex_write(sock_f_out, code, buf, len);
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

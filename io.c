/* -*- c-file-style: "linux" -*-
 *
 * Copyright (C) 1996-2001 by Andrew Tridgell
 * Copyright (C) Paul Mackerras 1996
 * Copyright (C) 2001, 2002 by Martin Pool <mbp@samba.org>
 *
 * This program is free software; you can redistribute it and/or modify
 * it under the terms of the GNU General Public License as published by
 * the Free Software Foundation; either version 2 of the License, or
 * (at your option) any later version.
 *
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License
 * along with this program; if not, write to the Free Software
 * Foundation, Inc., 675 Mass Ave, Cambridge, MA 02139, USA.
 */

/**
 * @file io.c
 *
 * Socket and pipe I/O utilities used in rsync.
 *
 * rsync provides its own multiplexing system, which is used to send
 * stderr and stdout over a single socket.  We need this because
 * stdout normally carries the binary data stream, and stderr all our
 * error messages.
 *
 * For historical reasons this is off during the start of the
 * connection, but it's switched on quite early using
 * io_start_multiplex_out() and io_start_multiplex_in().
 **/

#include "rsync.h"

/** If no timeout is specified then use a 60 second select timeout */
#define SELECT_TIMEOUT 60

extern int bwlimit;
extern size_t bwlimit_writemax;
extern int verbose;
extern int io_timeout;
extern int am_server;
extern int am_daemon;
extern int am_sender;
extern int eol_nulls;
extern int checksum_seed;
extern int protocol_version;
extern char *remote_filesfrom_file;
extern struct stats stats;

const char phase_unknown[] = "unknown";
int select_timeout = SELECT_TIMEOUT;
int batch_fd = -1;
int batch_gen_fd = -1;

/**
 * The connection might be dropped at some point; perhaps because the
 * remote instance crashed.  Just giving the offset on the stream is
 * not very helpful.  So instead we try to make io_phase_name point to
 * something useful.
 *
 * For buffered/multiplexed I/O these names will be somewhat
 * approximate; perhaps for ease of support we would rather make the
 * buffer always flush when a single application-level I/O finishes.
 *
 * @todo Perhaps we want some simple stack functionality, but there's
 * no need to overdo it.
 **/
const char *io_write_phase = phase_unknown;
const char *io_read_phase = phase_unknown;

/** Ignore EOF errors while reading a module listing if the remote
    version is 24 or less. */
int kludge_around_eof = False;

int msg_fd_in = -1;
int msg_fd_out = -1;

static int io_multiplexing_out;
static int io_multiplexing_in;
static int sock_f_in = -1;
static int sock_f_out = -1;
static time_t last_io;
static int no_flush;

static int write_batch_monitor_in = -1;
static int write_batch_monitor_out = -1;

static int io_filesfrom_f_in = -1;
static int io_filesfrom_f_out = -1;
static char io_filesfrom_buf[2048];
static char *io_filesfrom_bp;
static char io_filesfrom_lastchar;
static int io_filesfrom_buflen;

static void read_loop(int fd, char *buf, size_t len);

struct redo_list {
	struct redo_list *next;
	int num;
};

static struct redo_list *redo_list_head;
static struct redo_list *redo_list_tail;

struct msg_list {
	struct msg_list *next;
	char *buf;
	int len;
};

static struct msg_list *msg_list_head;
static struct msg_list *msg_list_tail;

static void redo_list_add(int num)
{
	struct redo_list *rl;

	if (!(rl = new(struct redo_list)))
		exit_cleanup(RERR_MALLOC);
	rl->next = NULL;
	rl->num = num;
	if (redo_list_tail)
		redo_list_tail->next = rl;
	else
		redo_list_head = rl;
	redo_list_tail = rl;
}

static void check_timeout(void)
{
	time_t t;

	if (!io_timeout)
		return;

	if (!last_io) {
		last_io = time(NULL);
		return;
	}

	t = time(NULL);

	if (t - last_io >= io_timeout) {
		if (!am_server && !am_daemon) {
			rprintf(FERROR, "io timeout after %d seconds - exiting\n",
				(int)(t-last_io));
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

/** Setup the fd used to receive MSG_* messages.  Only needed when
 * we're the generator because the sender and receiver both use the
 * multiplexed I/O setup. */
void set_msg_fd_in(int fd)
{
	msg_fd_in = fd;
}

/** Setup the fd used to send our MSG_* messages.  Only needed when
 * we're the receiver because the generator and the sender both use
 * the multiplexed I/O setup. */
void set_msg_fd_out(int fd)
{
	msg_fd_out = fd;
	set_nonblocking(msg_fd_out);
}

/* Add a message to the pending MSG_* list. */
static void msg_list_add(int code, char *buf, int len)
{
	struct msg_list *ml;

	if (!(ml = new(struct msg_list)))
		exit_cleanup(RERR_MALLOC);
	ml->next = NULL;
	if (!(ml->buf = new_array(char, len+4)))
		exit_cleanup(RERR_MALLOC);
	SIVAL(ml->buf, 0, ((code+MPLEX_BASE)<<24) | len);
	memcpy(ml->buf+4, buf, len);
	ml->len = len+4;
	if (msg_list_tail)
		msg_list_tail->next = ml;
	else
		msg_list_head = ml;
	msg_list_tail = ml;
}

void send_msg(enum msgcode code, char *buf, int len)
{
	msg_list_add(code, buf, len);
	msg_list_push(NORMAL_FLUSH);
}

/** Read a message from the MSG_* fd and dispatch it.  This is only
 * called by the generator. */
static void read_msg_fd(void)
{
	char buf[2048];
	size_t n;
	int fd = msg_fd_in;
	int tag, len;

	/* Temporarily disable msg_fd_in.  This is needed to avoid looping back
	 * to this routine from read_timeout() and writefd_unbuffered(). */
	msg_fd_in = -1;

	read_loop(fd, buf, 4);
	tag = IVAL(buf, 0);

	len = tag & 0xFFFFFF;
	tag = (tag >> 24) - MPLEX_BASE;

	switch (tag) {
	case MSG_DONE:
		if (len != 0) {
			rprintf(FERROR, "invalid message %d:%d\n", tag, len);
			exit_cleanup(RERR_STREAMIO);
		}
		redo_list_add(-1);
		break;
	case MSG_REDO:
		if (len != 4) {
			rprintf(FERROR, "invalid message %d:%d\n", tag, len);
			exit_cleanup(RERR_STREAMIO);
		}
		read_loop(fd, buf, 4);
		redo_list_add(IVAL(buf,0));
		break;
	case MSG_INFO:
	case MSG_ERROR:
	case MSG_LOG:
		while (len) {
			n = len;
			if (n >= sizeof buf)
				n = sizeof buf - 1;
			read_loop(fd, buf, n);
			rwrite((enum logcode)tag, buf, n);
			len -= n;
		}
		break;
	default:
		rprintf(FERROR, "unknown message %d:%d\n", tag, len);
		exit_cleanup(RERR_STREAMIO);
	}

	msg_fd_in = fd;
}

/* Try to push messages off the list onto the wire.  If we leave with more
 * to do, return 0.  On error, return -1.  If everything flushed, return 1.
 * This is only active in the receiver. */
int msg_list_push(int flush_it_all)
{
	static int written = 0;
	struct timeval tv;
	fd_set fds;

	if (msg_fd_out < 0)
		return -1;

	while (msg_list_head) {
		struct msg_list *ml = msg_list_head;
		int n = write(msg_fd_out, ml->buf + written, ml->len - written);
		if (n < 0) {
			if (errno == EINTR)
				continue;
			if (errno != EWOULDBLOCK && errno != EAGAIN)
				return -1;
			if (!flush_it_all)
				return 0;
			FD_ZERO(&fds);
			FD_SET(msg_fd_out, &fds);
			tv.tv_sec = select_timeout;
			tv.tv_usec = 0;
			if (!select(msg_fd_out+1, NULL, &fds, NULL, &tv))
				check_timeout();
		} else if ((written += n) == ml->len) {
			free(ml->buf);
			msg_list_head = ml->next;
			if (!msg_list_head)
				msg_list_tail = NULL;
			free(ml);
			written = 0;
		}
	}
	return 1;
}

int get_redo_num(void)
{
	struct redo_list *next;
	int num;

	while (!redo_list_head)
		read_msg_fd();

	num = redo_list_head->num;
	next = redo_list_head->next;
	free(redo_list_head);
	redo_list_head = next;
	if (!next)
		redo_list_tail = NULL;

	return num;
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

/**
 * It's almost always an error to get an EOF when we're trying to read
 * from the network, because the protocol is self-terminating.
 *
 * However, there is one unfortunate cases where it is not, which is
 * rsync <2.4.6 sending a list of modules on a server, since the list
 * is terminated by closing the socket. So, for the section of the
 * program where that is a problem (start_socket_client),
 * kludge_around_eof is True and we just exit.
 */
static void whine_about_eof(int fd)
{
	if (kludge_around_eof && fd == sock_f_in)
		exit_cleanup(0);

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
	int n, ret = 0;

	io_flush(NORMAL_FLUSH);

	while (ret == 0) {
		/* until we manage to read *something* */
		fd_set r_fds, w_fds;
		struct timeval tv;
		int maxfd = fd;
		int count;

		FD_ZERO(&r_fds);
		FD_ZERO(&w_fds);
		FD_SET(fd, &r_fds);
		if (msg_fd_in >= 0) {
			FD_SET(msg_fd_in, &r_fds);
			if (msg_fd_in > maxfd)
				maxfd = msg_fd_in;
		} else if (msg_list_head) {
			FD_SET(msg_fd_out, &w_fds);
			if (msg_fd_out > maxfd)
				maxfd = msg_fd_out;
		}
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
			if (errno == EBADF)
				exit_cleanup(RERR_SOCKETIO);
			check_timeout();
			continue;
		}

		if (msg_fd_in >= 0 && FD_ISSET(msg_fd_in, &r_fds))
			read_msg_fd();
		else if (msg_list_head && FD_ISSET(msg_fd_out, &w_fds))
			msg_list_push(NORMAL_FLUSH);

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
			if (fd == sock_f_in)
				close_multiplexing_out();
			rsyserr(FERROR, errno, "read error");
			exit_cleanup(RERR_STREAMIO);
		}

		buf += n;
		len -= n;
		ret += n;

		if (io_timeout && fd == sock_f_in)
			last_io = time(NULL);
	}

	return ret;
}

/**
 * Read a line into the "fname" buffer (which must be at least MAXPATHLEN
 * characters long).
 */
int read_filesfrom_line(int fd, char *fname)
{
	char ch, *s, *eob = fname + MAXPATHLEN - 1;
	int cnt;
	int reading_remotely = remote_filesfrom_file != NULL;
	int nulls = eol_nulls || reading_remotely;

  start:
	s = fname;
	while (1) {
		cnt = read(fd, &ch, 1);
		if (cnt < 0 && (errno == EWOULDBLOCK
		  || errno == EINTR || errno == EAGAIN)) {
			struct timeval tv;
			fd_set fds;
			FD_ZERO(&fds);
			FD_SET(fd, &fds);
			tv.tv_sec = select_timeout;
			tv.tv_usec = 0;
			if (!select(fd+1, &fds, NULL, NULL, &tv))
				check_timeout();
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


static char *iobuf_out;
static int iobuf_out_cnt;

void io_start_buffering_out(void)
{
	if (iobuf_out)
		return;
	if (!(iobuf_out = new_array(char, IO_BUFFER_SIZE)))
		out_of_memory("io_start_buffering_out");
	iobuf_out_cnt = 0;
}


static char *iobuf_in;
static size_t iobuf_in_siz;

void io_start_buffering_in(void)
{
	if (iobuf_in)
		return;
	iobuf_in_siz = 2 * IO_BUFFER_SIZE;
	if (!(iobuf_in = new_array(char, iobuf_in_siz)))
		out_of_memory("io_start_buffering_in");
}


void io_end_buffering(void)
{
	io_flush(NORMAL_FLUSH);
	if (!io_multiplexing_out) {
		free(iobuf_out);
		iobuf_out = NULL;
	}
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
	static size_t remaining;
	static size_t iobuf_in_ndx;
	int tag, ret = 0;
	char line[1024];

	if (!iobuf_in || fd != sock_f_in)
		return read_timeout(fd, buf, len);

	if (!io_multiplexing_in && remaining == 0) {
		remaining = read_timeout(fd, iobuf_in, iobuf_in_siz);
		iobuf_in_ndx = 0;
	}

	while (ret == 0) {
		if (remaining) {
			len = MIN(len, remaining);
			memcpy(buf, iobuf_in + iobuf_in_ndx, len);
			iobuf_in_ndx += len;
			remaining -= len;
			ret = len;
			break;
		}

		read_loop(fd, line, 4);
		tag = IVAL(line, 0);

		remaining = tag & 0xFFFFFF;
		tag = (tag >> 24) - MPLEX_BASE;

		switch (tag) {
		case MSG_DATA:
			if (remaining > iobuf_in_siz) {
				if (!(iobuf_in = realloc_array(iobuf_in, char,
							       remaining)))
					out_of_memory("readfd_unbuffered");
				iobuf_in_siz = remaining;
			}
			read_loop(fd, iobuf_in, remaining);
			iobuf_in_ndx = 0;
			break;
		case MSG_INFO:
		case MSG_ERROR:
			if (remaining >= sizeof line) {
				rprintf(FERROR, "multiplexing overflow %d:%ld\n\n",
					tag, (long)remaining);
				exit_cleanup(RERR_STREAMIO);
			}
			read_loop(fd, line, remaining);
			rwrite((enum logcode)tag, line, remaining);
			remaining = 0;
			break;
		default:
			rprintf(FERROR, "unexpected tag %d\n", tag);
			exit_cleanup(RERR_STREAMIO);
		}
	}

	if (remaining == 0)
		io_flush(NORMAL_FLUSH);

	return ret;
}



/**
 * Do a buffered read from @p fd.  Don't return until all @p n bytes
 * have been read.  If all @p n can't be read then exit with an
 * error.
 **/
static void readfd(int fd, char *buffer, size_t N)
{
	int  ret;
	size_t total = 0;

	while (total < N) {
		ret = readfd_unbuffered(fd, buffer + total, N-total);
		total += ret;
	}

	if (fd == write_batch_monitor_in) {
		if ((size_t)write(batch_fd, buffer, total) != total)
			exit_cleanup(RERR_FILEIO);
	}

	if (fd == sock_f_in)
		stats.total_read += total;
}


int32 read_int(int f)
{
	char b[4];
	int32 ret;

	readfd(f,b,4);
	ret = IVAL(b,0);
	if (ret == (int32)0xffffffff)
		return -1;
	return ret;
}

int64 read_longint(int f)
{
	int64 ret;
	char b[8];
	ret = read_int(f);

	if ((int32)ret != (int32)0xffffffff)
		return ret;

#ifdef INT64_IS_OFF_T
	if (sizeof (int64) < 8) {
		rprintf(FERROR, "Integer overflow: attempted 64-bit offset\n");
		exit_cleanup(RERR_UNSUPPORTED);
	}
#endif
	readfd(f,b,8);
	ret = IVAL(b,0) | (((int64)IVAL(b,4))<<32);

	return ret;
}

void read_buf(int f,char *buf,size_t len)
{
	readfd(f,buf,len);
}

void read_sbuf(int f,char *buf,size_t len)
{
	readfd(f, buf, len);
	buf[len] = 0;
}

unsigned char read_byte(int f)
{
	unsigned char c;
	readfd(f, (char *)&c, 1);
	return c;
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

	if (!bwlimit)
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
 * the job done and also (in the generator) reading any data on msg_fd_in
 * (to avoid deadlock).
 *
 * This function underlies the multiplexing system.  The body of the
 * application never calls this function directly. */
static void writefd_unbuffered(int fd,char *buf,size_t len)
{
	size_t n, total = 0;
	fd_set w_fds, r_fds;
	int maxfd, count, ret;
	struct timeval tv;

	no_flush++;

	while (total < len) {
		FD_ZERO(&w_fds);
		FD_SET(fd,&w_fds);
		maxfd = fd;

		if (msg_fd_in >= 0) {
			FD_ZERO(&r_fds);
			FD_SET(msg_fd_in,&r_fds);
			if (msg_fd_in > maxfd)
				maxfd = msg_fd_in;
		}

		tv.tv_sec = select_timeout;
		tv.tv_usec = 0;

		errno = 0;
		count = select(maxfd + 1, msg_fd_in >= 0 ? &r_fds : NULL,
			       &w_fds, NULL, &tv);

		if (count <= 0) {
			if (count < 0 && errno == EBADF)
				exit_cleanup(RERR_SOCKETIO);
			check_timeout();
			continue;
		}

		if (msg_fd_in >= 0 && FD_ISSET(msg_fd_in, &r_fds))
			read_msg_fd();

		if (!FD_ISSET(fd, &w_fds))
			continue;

		n = len - total;
		if (bwlimit && n > bwlimit_writemax)
			n = bwlimit_writemax;
		ret = write(fd, buf + total, n);

		if (ret <= 0) {
			if (ret < 0) {
				if (errno == EINTR)
					continue;
				if (errno == EWOULDBLOCK || errno == EAGAIN) {
					msleep(1);
					continue;
				}
			}

			/* Don't try to write errors back across the stream. */
			if (fd == sock_f_out)
				close_multiplexing_out();
			rsyserr(FERROR, errno,
				"writefd_unbuffered failed to write %ld bytes: phase \"%s\" [%s]",
				(long)len, io_write_phase, who_am_i());
			/* If the other side is sending us error messages, try
			 * to grab any messages they sent before they died. */
			while (fd == sock_f_out && io_multiplexing_in) {
				io_timeout = 30;
				readfd_unbuffered(sock_f_in, io_filesfrom_buf,
						  sizeof io_filesfrom_buf);
			}
			exit_cleanup(RERR_STREAMIO);
		}

		total += ret;

		if (fd == sock_f_out) {
			if (io_timeout)
				last_io = time(NULL);
			sleep_for_bwlimit(ret);
		}
	}

	no_flush--;
}


/**
 * Write an message to a multiplexed stream. If this fails then rsync
 * exits.
 **/
static void mplex_write(enum msgcode code, char *buf, size_t len)
{
	char buffer[4096];
	size_t n = len;

	SIVAL(buffer, 0, ((MPLEX_BASE + (int)code)<<24) + len);

	if (n > sizeof buffer - 4)
		n = sizeof buffer - 4;

	memcpy(&buffer[4], buf, n);
	writefd_unbuffered(sock_f_out, buffer, n+4);

	len -= n;
	buf += n;

	if (len)
		writefd_unbuffered(sock_f_out, buf, len);
}


void io_flush(int flush_it_all)
{
	msg_list_push(flush_it_all);

	if (!iobuf_out_cnt || no_flush)
		return;

	if (io_multiplexing_out)
		mplex_write(MSG_DATA, iobuf_out, iobuf_out_cnt);
	else
		writefd_unbuffered(sock_f_out, iobuf_out, iobuf_out_cnt);
	iobuf_out_cnt = 0;
}


static void writefd(int fd,char *buf,size_t len)
{
	if (fd == msg_fd_out) {
		rprintf(FERROR, "Internal error: wrong write used in receiver.\n");
		exit_cleanup(RERR_PROTOCOL);
	}

	if (fd == sock_f_out)
		stats.total_written += len;

	if (fd == write_batch_monitor_out) {
		if ((size_t)write(batch_fd, buf, len) != len)
			exit_cleanup(RERR_FILEIO);
	}

	if (!iobuf_out || fd != sock_f_out) {
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


void write_int(int f,int32 x)
{
	char b[4];
	SIVAL(b,0,x);
	writefd(f,b,4);
}


void write_int_named(int f, int32 x, const char *phase)
{
	io_write_phase = phase;
	write_int(f, x);
	io_write_phase = phase_unknown;
}


/*
 * Note: int64 may actually be a 32-bit type if ./configure couldn't find any
 * 64-bit types on this platform.
 */
void write_longint(int f, int64 x)
{
	char b[8];

	if (x <= 0x7FFFFFFF) {
		write_int(f, (int)x);
		return;
	}

#ifdef INT64_IS_OFF_T
	if (sizeof (int64) < 8) {
		rprintf(FERROR, "Integer overflow: attempted 64-bit offset\n");
		exit_cleanup(RERR_UNSUPPORTED);
	}
#endif

	write_int(f, (int32)0xFFFFFFFF);
	SIVAL(b,0,(x&0xFFFFFFFF));
	SIVAL(b,4,((x>>32)&0xFFFFFFFF));

	writefd(f,b,8);
}

void write_buf(int f,char *buf,size_t len)
{
	writefd(f,buf,len);
}

/** Write a string to the connection */
void write_sbuf(int f, char *buf)
{
	writefd(f, buf, strlen(buf));
}

void write_byte(int f,unsigned char c)
{
	writefd(f, (char *)&c, 1);
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
	char buf[1024];
	int len;

	va_start(ap, format);
	len = vsnprintf(buf, sizeof buf, format, ap);
	va_end(ap);

	if (len < 0)
		exit_cleanup(RERR_STREAMIO);

	write_sbuf(fd, buf);
}


/** Setup for multiplexing a MSG_* stream with the data stream. */
void io_start_multiplex_out(void)
{
	io_flush(NORMAL_FLUSH);
	io_start_buffering_out();
	io_multiplexing_out = 1;
}

/** Setup for multiplexing a MSG_* stream with the data stream. */
void io_start_multiplex_in(void)
{
	io_flush(NORMAL_FLUSH);
	io_start_buffering_in();
	io_multiplexing_in = 1;
}

/** Write an message to the multiplexed data stream. */
int io_multiplex_write(enum msgcode code, char *buf, size_t len)
{
	if (!io_multiplexing_out)
		return 0;

	io_flush(NORMAL_FLUSH);
	stats.total_written += (len+4);
	mplex_write(code, buf, len);
	return 1;
}

void close_multiplexing_in(void)
{
	io_multiplexing_in = 0;
}

/** Stop output multiplexing. */
void close_multiplexing_out(void)
{
	io_multiplexing_out = 0;
}

void start_write_batch(int fd)
{
	write_stream_flags(batch_fd);

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

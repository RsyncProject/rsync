/* -*- c-file-style: "linux" -*-
   
   Copyright (C) 1996-2001 by Andrew Tridgell 
   Copyright (C) Paul Mackerras 1996
   Copyright (C) 2001 by Martin Pool <mbp@samba.org>
   
   This program is free software; you can redistribute it and/or modify
   it under the terms of the GNU General Public License as published by
   the Free Software Foundation; either version 2 of the License, or
   (at your option) any later version.
   
   This program is distributed in the hope that it will be useful,
   but WITHOUT ANY WARRANTY; without even the implied warranty of
   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
   GNU General Public License for more details.
   
   You should have received a copy of the GNU General Public License
   along with this program; if not, write to the Free Software
   Foundation, Inc., 675 Mass Ave, Cambridge, MA 02139, USA.
*/

/*
  socket and pipe IO utilities used in rsync 

  tridge, June 1996
  */
#include "rsync.h"

/* if no timeout is specified then use a 60 second select timeout */
#define SELECT_TIMEOUT 60

static int io_multiplexing_out;
static int io_multiplexing_in;
static int multiplex_in_fd;
static int multiplex_out_fd;
static time_t last_io;
static int no_flush;

extern int bwlimit;
extern int verbose;
extern int io_timeout;
extern struct stats stats;


/** Ignore EOF errors while reading a module listing if the remote
    version is 24 or less. */
int kludge_around_eof = False;


static int io_error_fd = -1;

static void read_loop(int fd, char *buf, size_t len);

static void check_timeout(void)
{
	extern int am_server, am_daemon;
	time_t t;

	err_list_push();
	
	if (!io_timeout) return;

	if (!last_io) {
		last_io = time(NULL);
		return;
	}

	t = time(NULL);

	if (last_io && io_timeout && (t-last_io) >= io_timeout) {
		if (!am_server && !am_daemon) {
			rprintf(FERROR,"io timeout after %d seconds - exiting\n", 
				(int)(t-last_io));
		}
		exit_cleanup(RERR_TIMEOUT);
	}
}

/* setup the fd used to propogate errors */
void io_set_error_fd(int fd)
{
	io_error_fd = fd;
}

/* read some data from the error fd and write it to the write log code */
static void read_error_fd(void)
{
	char buf[200];
	size_t n;
	int fd = io_error_fd;
	int tag, len;

        /* io_error_fd is temporarily disabled -- is this meant to
         * prevent indefinite recursion? */
	io_error_fd = -1;

	read_loop(fd, buf, 4);
	tag = IVAL(buf, 0);

	len = tag & 0xFFFFFF;
	tag = tag >> 24;
	tag -= MPLEX_BASE;

	while (len) {
		n = len;
		if (n > (sizeof(buf)-1))
			n = sizeof(buf)-1;
		read_loop(fd, buf, n);
		rwrite((enum logcode)tag, buf, n);
		len -= n;
	}

	io_error_fd = fd;
}


static void whine_about_eof (void)
{
	/**
	   It's almost always an error to get an EOF when we're trying
	   to read from the network, because the protocol is
	   self-terminating.
	   
	   However, there is one unfortunate cases where it is not,
	   which is rsync <2.4.6 sending a list of modules on a
	   server, since the list is terminated by closing the socket.
	   So, for the section of the program where that is a problem
	   (start_socket_client), kludge_around_eof is True and we
	   just exit.
	*/

	if (kludge_around_eof)
		exit_cleanup (0);
	else {
		rprintf (FERROR,
			 "%s: connection unexpectedly closed "
			 "(%.0f bytes read so far)\n",
			 RSYNC_NAME, (double)stats.total_read);
	
		exit_cleanup (RERR_STREAMIO);
	}
}


static void die_from_readerr (int err)
{
	/* this prevents us trying to write errors on a dead socket */
	io_multiplexing_close();
				
	rprintf(FERROR, "%s: read error: %s\n",
		RSYNC_NAME, strerror (err));
	exit_cleanup(RERR_STREAMIO);
}


/*!
 * Read from a socket with IO timeout. return the number of bytes
 * read. If no bytes can be read then exit, never return a number <= 0.
 *
 * TODO: If the remote shell connection fails, then current versions
 * actually report an "unexpected EOF" error here.  Since it's a
 * fairly common mistake to try to use rsh when ssh is required, we
 * should trap that: if we fail to read any data at all, we should
 * give a better explanation.  We can tell whether the connection has
 * started by looking e.g. at whether the remote version is known yet.
 */
static int read_timeout (int fd, char *buf, size_t len)
{
	int n, ret=0;

	io_flush();

	while (ret == 0) {
		/* until we manage to read *something* */
		fd_set fds;
		struct timeval tv;
		int fd_count = fd+1;
		int count;

		FD_ZERO(&fds);
		FD_SET(fd, &fds);
		if (io_error_fd != -1) {
			FD_SET(io_error_fd, &fds);
			if (io_error_fd > fd) fd_count = io_error_fd+1;
		}

		tv.tv_sec = io_timeout?io_timeout:SELECT_TIMEOUT;
		tv.tv_usec = 0;

		errno = 0;

		count = select(fd_count, &fds, NULL, NULL, &tv);

		if (count == 0) {
			check_timeout();
		}

		if (count <= 0) {
			if (errno == EBADF) {
				exit_cleanup(RERR_SOCKETIO);
			}
			continue;
		}

		if (io_error_fd != -1 && FD_ISSET(io_error_fd, &fds)) {
			read_error_fd();
		}

		if (!FD_ISSET(fd, &fds)) continue;

		n = read(fd, buf, len);

		if (n > 0) {
			buf += n;
			len -= n;
			ret += n;
			if (io_timeout)
				last_io = time(NULL);
			continue;
		} else if (n == 0) {
			whine_about_eof ();
			return -1; /* doesn't return */
		} else if (n == -1) {
			if (errno == EINTR || errno == EWOULDBLOCK ||
			    errno == EAGAIN) 
				continue;
			else
				die_from_readerr (errno);
		}
	}

	return ret;
}




/*! Continue trying to read len bytes - don't return until len has
  been read.   */
static void read_loop (int fd, char *buf, size_t len)
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
static int read_unbuffered(int fd, char *buf, size_t len)
{
	static size_t remaining;
	int tag, ret = 0;
	char line[1024];

	if (!io_multiplexing_in || fd != multiplex_in_fd)
		return read_timeout(fd, buf, len);

	while (ret == 0) {
		if (remaining) {
			len = MIN(len, remaining);
			read_loop(fd, buf, len);
			remaining -= len;
			ret = len;
			continue;
		}

		read_loop(fd, line, 4);
		tag = IVAL(line, 0);

		remaining = tag & 0xFFFFFF;
		tag = tag >> 24;

		if (tag == MPLEX_BASE)
			continue;

		tag -= MPLEX_BASE;

		if (tag != FERROR && tag != FINFO) {
			rprintf(FERROR, "unexpected tag %d\n", tag);
			exit_cleanup(RERR_STREAMIO);
		}

		if (remaining > sizeof(line) - 1) {
			rprintf(FERROR, "multiplexing overflow %d\n\n",
				remaining);
			exit_cleanup(RERR_STREAMIO);
		}

		read_loop(fd, line, remaining);
		line[remaining] = 0;

		rprintf((enum logcode) tag, "%s", line);
		remaining = 0;
	}

	return ret;
}



/* do a buffered read from fd. don't return until all N bytes
   have been read. If all N can't be read then exit with an error */
static void readfd (int fd, char *buffer, size_t N)
{
	int  ret;
	size_t total=0;  
	
	while (total < N) {
		io_flush();

		ret = read_unbuffered (fd, buffer + total, N-total);
		total += ret;
	}

	stats.total_read += total;
}


int32 read_int(int f)
{
	char b[4];
	int32 ret;

	readfd(f,b,4);
	ret = IVAL(b,0);
	if (ret == (int32)0xffffffff) return -1;
	return ret;
}

int64 read_longint(int f)
{
	extern int remote_version;
	int64 ret;
	char b[8];
	ret = read_int(f);

	if ((int32)ret != (int32)0xffffffff) {
		return ret;
	}

#ifdef NO_INT64
	rprintf(FERROR,"Integer overflow - attempted 64 bit offset\n");
	exit_cleanup(RERR_UNSUPPORTED);
#else
	if (remote_version >= 16) {
		readfd(f,b,8);
		ret = IVAL(b,0) | (((int64)IVAL(b,4))<<32);
	}
#endif

	return ret;
}

void read_buf(int f,char *buf,size_t len)
{
	readfd(f,buf,len);
}

void read_sbuf(int f,char *buf,size_t len)
{
	read_buf (f,buf,len);
	buf[len] = 0;
}

unsigned char read_byte(int f)
{
	unsigned char c;
	read_buf (f, (char *)&c, 1);
	return c;
}

/* write len bytes to fd */
static void writefd_unbuffered(int fd,char *buf,size_t len)
{
	size_t total = 0;
	fd_set w_fds, r_fds;
	int fd_count, count;
	struct timeval tv;

	err_list_push();

	no_flush++;

	while (total < len) {
		FD_ZERO(&w_fds);
		FD_ZERO(&r_fds);
		FD_SET(fd,&w_fds);
		fd_count = fd;

		if (io_error_fd != -1) {
			FD_SET(io_error_fd,&r_fds);
			if (io_error_fd > fd_count) 
				fd_count = io_error_fd;
		}

		tv.tv_sec = io_timeout?io_timeout:SELECT_TIMEOUT;
		tv.tv_usec = 0;

		errno = 0;

		count = select(fd_count+1,
			       io_error_fd != -1?&r_fds:NULL,
			       &w_fds,NULL,
			       &tv);

		if (count == 0) {
			check_timeout();
		}

		if (count <= 0) {
			if (errno == EBADF) {
				exit_cleanup(RERR_SOCKETIO);
			}
			continue;
		}

		if (io_error_fd != -1 && FD_ISSET(io_error_fd, &r_fds)) {
			read_error_fd();
		}

		if (FD_ISSET(fd, &w_fds)) {
			int ret;
			size_t n = len-total;
			ret = write(fd,buf+total,n);

			if (ret == -1 && errno == EINTR) {
				continue;
			}

			if (ret == -1 && 
			    (errno == EWOULDBLOCK || errno == EAGAIN)) {
				msleep(1);
				continue;
			}

			if (ret <= 0) {
				rprintf(FERROR,
					"error writing %d unbuffered bytes"
					" - exiting: %s\n", len,
					strerror(errno));
				exit_cleanup(RERR_STREAMIO);
			}

			/* Sleep after writing to limit I/O bandwidth */
			if (bwlimit)
			{
			    tv.tv_sec = 0;
			    tv.tv_usec = ret * 1000 / bwlimit;
			    while (tv.tv_usec > 1000000)
			    {
				tv.tv_sec++;
				tv.tv_usec -= 1000000;
			    }
			    select(0, NULL, NULL, NULL, &tv);
 			}
 
			total += ret;

			if (io_timeout)
				last_io = time(NULL);
		}
	}

	no_flush--;
}


static char *io_buffer;
static int io_buffer_count;

void io_start_buffering(int fd)
{
	if (io_buffer) return;
	multiplex_out_fd = fd;
	io_buffer = (char *)malloc(IO_BUFFER_SIZE);
	if (!io_buffer) out_of_memory("writefd");
	io_buffer_count = 0;
}

/* write an message to a multiplexed stream. If this fails then rsync
   exits */
static void mplex_write(int fd, enum logcode code, char *buf, size_t len)
{
	char buffer[4096];
	size_t n = len;

	SIVAL(buffer, 0, ((MPLEX_BASE + (int)code)<<24) + len);

	if (n > (sizeof(buffer)-4)) {
		n = sizeof(buffer)-4;
	}

	memcpy(&buffer[4], buf, n);
	writefd_unbuffered(fd, buffer, n+4);

	len -= n;
	buf += n;

	if (len) {
		writefd_unbuffered(fd, buf, len);
	}
}


void io_flush(void)
{
	int fd = multiplex_out_fd;

	err_list_push();

	if (!io_buffer_count || no_flush) return;

	if (io_multiplexing_out) {
		mplex_write(fd, FNONE, io_buffer, io_buffer_count);
	} else {
		writefd_unbuffered(fd, io_buffer, io_buffer_count);
	}
	io_buffer_count = 0;
}


void io_end_buffering(void)
{
	io_flush();
	if (!io_multiplexing_out) {
		free(io_buffer);
		io_buffer = NULL;
	}
}

static void writefd(int fd,char *buf,size_t len)
{
	stats.total_written += len;

	err_list_push();

	if (!io_buffer || fd != multiplex_out_fd) {
		writefd_unbuffered(fd, buf, len);
		return;
	}

	while (len) {
		int n = MIN((int) len, IO_BUFFER_SIZE-io_buffer_count);
		if (n > 0) {
			memcpy(io_buffer+io_buffer_count, buf, n);
			buf += n;
			len -= n;
			io_buffer_count += n;
		}
		
		if (io_buffer_count == IO_BUFFER_SIZE) io_flush();
	}
}


void write_int(int f,int32 x)
{
	char b[4];
	SIVAL(b,0,x);
	writefd(f,b,4);
}


/*
 * Note: int64 may actually be a 32-bit type if ./configure couldn't find any
 * 64-bit types on this platform.
 */
void write_longint(int f, int64 x)
{
	extern int remote_version;
	char b[8];

	if (remote_version < 16 || x <= 0x7FFFFFFF) {
		write_int(f, (int)x);
		return;
	}

	write_int(f, (int32)0xFFFFFFFF);
	SIVAL(b,0,(x&0xFFFFFFFF));
	SIVAL(b,4,((x>>32)&0xFFFFFFFF));

	writefd(f,b,8);
}

void write_buf(int f,char *buf,size_t len)
{
	writefd(f,buf,len);
}

/* write a string to the connection */
static void write_sbuf(int f,char *buf)
{
	write_buf(f, buf, strlen(buf));
}


void write_byte(int f,unsigned char c)
{
	write_buf(f,(char *)&c,1);
}



int read_line(int f, char *buf, size_t maxlen)
{
	while (maxlen) {
		buf[0] = 0;
		read_buf(f, buf, 1);
		if (buf[0] == 0) return 0;
		if (buf[0] == '\n') {
			buf[0] = 0;
			break;
		}
		if (buf[0] != '\r') {
			buf++;
			maxlen--;
		}
	}
	if (maxlen == 0) {
		*buf = 0;
		return 0;
	}

	return 1;
}


void io_printf(int fd, const char *format, ...)
{
	va_list ap;  
	char buf[1024];
	int len;
	
	va_start(ap, format);
	len = vsnprintf(buf, sizeof(buf), format, ap);
	va_end(ap);

	if (len < 0) exit_cleanup(RERR_STREAMIO);

	write_sbuf(fd, buf);
}


/* setup for multiplexing an error stream with the data stream */
void io_start_multiplex_out(int fd)
{
	multiplex_out_fd = fd;
	io_flush();
	io_start_buffering(fd);
	io_multiplexing_out = 1;
}

/* setup for multiplexing an error stream with the data stream */
void io_start_multiplex_in(int fd)
{
	multiplex_in_fd = fd;
	io_flush();
	io_multiplexing_in = 1;
}

/* write an message to the multiplexed error stream */
int io_multiplex_write(enum logcode code, char *buf, size_t len)
{
	if (!io_multiplexing_out) return 0;

	io_flush();
	stats.total_written += (len+4);
	mplex_write(multiplex_out_fd, code, buf, len);
	return 1;
}

/* stop output multiplexing */
void io_multiplexing_close(void)
{
	io_multiplexing_out = 0;
}


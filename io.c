/* 
   Copyright (C) Andrew Tridgell 1996
   Copyright (C) Paul Mackerras 1996
   
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
static int eof_error=1;
extern int verbose;
extern int io_timeout;
extern struct stats stats;

static int buffer_f_in = -1;
static int io_error_fd = -1;

static void read_loop(int fd, char *buf, int len);

void setup_readbuffer(int f_in)
{
	buffer_f_in = f_in;
}

static void check_timeout(void)
{
	time_t t;
	
	if (!io_timeout) return;

	if (!last_io) {
		last_io = time(NULL);
		return;
	}

	t = time(NULL);

	if (last_io && io_timeout && (t-last_io) >= io_timeout) {
		rprintf(FERROR,"io timeout after %d second - exiting\n", 
			(int)(t-last_io));
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
	int n;
	int fd = io_error_fd;
	int tag, len;

	io_error_fd = -1;

	read_loop(fd, buf, 4);
	tag = IVAL(buf, 0);

	len = tag & 0xFFFFFF;
	tag = tag >> 24;
	tag -= MPLEX_BASE;

	while (len) {
		n = len;
		if (n > (sizeof(buf)-1)) n = sizeof(buf)-1;
		read_loop(fd, buf, n);
		rwrite((enum logcode)tag, buf, n);
		len -= n;
	}

	io_error_fd = fd;
}


static int no_flush;

/* read from a socket with IO timeout. return the number of
   bytes read. If no bytes can be read then exit, never return
   a number <= 0 */
static int read_timeout(int fd, char *buf, int len)
{
	int n, ret=0;

	io_flush();

	while (ret == 0) {
		fd_set fds;
		struct timeval tv;
		int fd_count = fd+1;

		FD_ZERO(&fds);
		FD_SET(fd, &fds);
		if (io_error_fd != -1) {
			FD_SET(io_error_fd, &fds);
			if (io_error_fd > fd) fd_count = io_error_fd+1;
		}

		tv.tv_sec = io_timeout?io_timeout:SELECT_TIMEOUT;
		tv.tv_usec = 0;

		errno = 0;

		if (select(fd_count, &fds, NULL, NULL, &tv) < 1) {
			if (errno == EBADF) {
				exit_cleanup(RERR_SOCKETIO);
			}
			check_timeout();
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
		}

		if (n == -1 && errno == EINTR) {
			continue;
		}


		if (n == 0) {
			if (eof_error) {
				rprintf(FERROR,"unexpected EOF in read_timeout\n");
			}
			exit_cleanup(RERR_STREAMIO);
		}

		/* this prevents us trying to write errors on a dead socket */
		io_multiplexing_close();

		rprintf(FERROR,"read error: %s\n", strerror(errno));
		exit_cleanup(RERR_STREAMIO);
	}

	return ret;
}

/* continue trying to read len bytes - don't return until len
   has been read */
static void read_loop(int fd, char *buf, int len)
{
	while (len) {
		int n = read_timeout(fd, buf, len);

		buf += n;
		len -= n;
	}
}

/* read from the file descriptor handling multiplexing - 
   return number of bytes read
   never return <= 0 */
static int read_unbuffered(int fd, char *buf, int len)
{
	static int remaining;
	int tag, ret=0;
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

		if (tag == MPLEX_BASE) continue;

		tag -= MPLEX_BASE;

		if (tag != FERROR && tag != FINFO) {
			rprintf(FERROR,"unexpected tag %d\n", tag);
			exit_cleanup(RERR_STREAMIO);
		}

		if (remaining > sizeof(line)-1) {
			rprintf(FERROR,"multiplexing overflow %d\n\n", 
				remaining);
			exit_cleanup(RERR_STREAMIO);
		}

		read_loop(fd, line, remaining);
		line[remaining] = 0;

		rprintf((enum logcode)tag,"%s", line);
		remaining = 0;
	}

	return ret;
}


/* do a buffered read from fd. don't return until all N bytes
   have been read. If all N can't be read then exit with an error */
static void readfd(int fd,char *buffer,int N)
{
	int  ret;
	int total=0;  
	
	while (total < N) {
		io_flush();

		ret = read_unbuffered(fd,buffer + total,N-total);
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

void read_buf(int f,char *buf,int len)
{
	readfd(f,buf,len);
}

void read_sbuf(int f,char *buf,int len)
{
	read_buf(f,buf,len);
	buf[len] = 0;
}

unsigned char read_byte(int f)
{
	unsigned char c;
	read_buf(f,(char *)&c,1);
	return c;
}



/* write len bytes to fd, possibly reading from buffer_f_in if set
   in order to unclog the pipe. don't return until all len
   bytes have been written */
static void writefd_unbuffered(int fd,char *buf,int len)
{
	int total = 0;
	fd_set w_fds, r_fds;
	int fd_count, count;
	struct timeval tv;

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

		if (count <= 0) {
			if (errno == EBADF) {
				exit_cleanup(RERR_SOCKETIO);
			}
			check_timeout();
			continue;
		}

		if (io_error_fd != -1 && FD_ISSET(io_error_fd, &r_fds)) {
			read_error_fd();
		}

		if (FD_ISSET(fd, &w_fds)) {
			int ret, n = len-total;
			
			if (n > PIPE_BUF) n = PIPE_BUF;

			ret = write(fd,buf+total,n?n:1);

			if (ret == -1 && errno == EINTR) {
				continue;
			}

			if (ret <= 0) {
				rprintf(FERROR,"erroring writing %d bytes - exiting\n", len);
				exit_cleanup(RERR_STREAMIO);
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
static void mplex_write(int fd, enum logcode code, char *buf, int len)
{
	char buffer[4096];
	int n = len;

	SIVAL(buffer, 0, ((MPLEX_BASE + (int)code)<<24) + len);

	if (n > (sizeof(buf)-4)) {
		n = sizeof(buf)-4;
	}

	memcpy(&buffer[4], buf, n);
	writefd_unbuffered(fd, buffer, n+4);

	len -= n;
	buf += n;

	writefd_unbuffered(fd, buf, len);
}


void io_flush(void)
{
	int fd = multiplex_out_fd;
	if (!io_buffer_count || no_flush) return;

	if (io_multiplexing_out) {
		mplex_write(fd, FNONE, io_buffer, io_buffer_count);
	} else {
		writefd_unbuffered(fd, io_buffer, io_buffer_count);
	}
	io_buffer_count = 0;
}

void io_end_buffering(int fd)
{
	io_flush();
	if (!io_multiplexing_out) {
		free(io_buffer);
		io_buffer = NULL;
	}
}

static void writefd(int fd,char *buf,int len)
{
	stats.total_written += len;

	if (!io_buffer || fd != multiplex_out_fd) {
		writefd_unbuffered(fd, buf, len);
		return;
	}

	while (len) {
		int n = MIN(len, IO_BUFFER_SIZE-io_buffer_count);
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

void write_buf(int f,char *buf,int len)
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

int read_line(int f, char *buf, int maxlen)
{
	eof_error = 0;

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

	eof_error = 1;

	return 1;
}


void io_printf(int fd, const char *format, ...)
{
	va_list ap;  
	char buf[1024];
	int len;
	
	va_start(ap, format);
	len = vslprintf(buf, sizeof(buf), format, ap);
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
int io_multiplex_write(enum logcode code, char *buf, int len)
{
	if (!io_multiplexing_out) return 0;

	io_flush();
	stats.total_written += (len+4);
	mplex_write(multiplex_out_fd, code, buf, len);
	return 1;
}

/* write a message to the special error fd */
int io_error_write(int f, enum logcode code, char *buf, int len)
{
	if (f == -1) return 0;
	mplex_write(f, code, buf, len);
	return 1;
}

/* stop output multiplexing */
void io_multiplexing_close(void)
{
	io_multiplexing_out = 0;
}

void io_close_input(int fd)
{
	buffer_f_in = -1;
}

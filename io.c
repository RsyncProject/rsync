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
  Utilities used in rsync 

  tridge, June 1996
  */
#include "rsync.h"

static int64 total_written;
static int64 total_read;

extern int verbose;
extern int sparse_files;
extern int io_timeout;

int64 write_total(void)
{
  return total_written;
}

int64 read_total(void)
{
  return total_read;
}

static int buffer_f_in = -1;

void setup_nonblocking(int f_in,int f_out)
{
  set_blocking(f_out,0);
  buffer_f_in = f_in;
}


static char *read_buffer;
static char *read_buffer_p;
static int read_buffer_len;
static int read_buffer_size;


/* This function was added to overcome a deadlock problem when using
 * ssh.  It looks like we can't allow our receive queue to get full or
 * ssh will clag up. Uggh.  */
static void read_check(int f)
{
  int n;

  if (f == -1) return;

  if (read_buffer_len == 0) {
    read_buffer_p = read_buffer;
  }

  if ((n=num_waiting(f)) <= 0)
    return;

  /* things could deteriorate if we read in really small chunks */
  if (n < 10) n = 1024;

  if (read_buffer_p != read_buffer) {
    memmove(read_buffer,read_buffer_p,read_buffer_len);
    read_buffer_p = read_buffer;
  }

  if (n > (read_buffer_size - read_buffer_len)) {
    read_buffer_size += n;
    if (!read_buffer)
      read_buffer = (char *)malloc(read_buffer_size);
    else
      read_buffer = (char *)realloc(read_buffer,read_buffer_size);
    if (!read_buffer) out_of_memory("read check");      
    read_buffer_p = read_buffer;      
  }

  n = read(f,read_buffer+read_buffer_len,n);
  if (n > 0) {
    read_buffer_len += n;
  }
}

static time_t last_io;


static void check_timeout(void)
{
	time_t t;
	
	if (!io_timeout) return;

	if (!last_io) {
		last_io = time(NULL);
		return;
	}

	t = time(NULL);

	if (last_io && io_timeout && (t-last_io)>io_timeout) {
		rprintf(FERROR,"read timeout after %d second - exiting\n", 
			(int)(t-last_io));
		exit_cleanup(1);
	}
}

static int readfd(int fd,char *buffer,int N)
{
	int  ret;
	int total=0;  
	struct timeval tv;
	
	if (read_buffer_len < N)
		read_check(buffer_f_in);
	
	while (total < N) {
		if (read_buffer_len > 0 && buffer_f_in == fd) {
			ret = MIN(read_buffer_len,N-total);
			memcpy(buffer+total,read_buffer_p,ret);
			read_buffer_p += ret;
			read_buffer_len -= ret;
			total += ret;
			continue;
		} 

		while ((ret = read(fd,buffer + total,N-total)) == -1) {
			fd_set fds;

			if (errno != EAGAIN && errno != EWOULDBLOCK)
				return -1;
			FD_ZERO(&fds);
			FD_SET(fd, &fds);
			tv.tv_sec = io_timeout;
			tv.tv_usec = 0;

			if (select(fd+1, &fds, NULL, NULL, 
				   io_timeout?&tv:NULL) != 1) {
				check_timeout();
			}
		}

		if (ret <= 0)
			return total;
		total += ret;
	}

	if (io_timeout)
		last_io = time(NULL);
	return total;
}


int32 read_int(int f)
{
  int ret;
  char b[4];
  if ((ret=readfd(f,b,4)) != 4) {
    if (verbose > 1) 
      rprintf(FERROR,"(%d) Error reading %d bytes : %s\n",
	      getpid(),4,ret==-1?strerror(errno):"EOF");
    exit_cleanup(1);
  }
  total_read += 4;
  return IVAL(b,0);
}

int64 read_longint(int f)
{
	extern int remote_version;
	int64 ret;
	char b[8];
	ret = read_int(f);

	if ((int32)ret != (int32)0xffffffff) return ret;

#ifdef NO_INT64
	rprintf(FERROR,"Integer overflow - attempted 64 bit offset\n");
	exit_cleanup(1);
#else
	if (remote_version >= 16) {
		if ((ret=readfd(f,b,8)) != 8) {
			if (verbose > 1) 
				rprintf(FERROR,"(%d) Error reading %d bytes : %s\n",
					getpid(),8,ret==-1?strerror(errno):"EOF");
			exit_cleanup(1);
		}
		total_read += 8;
		ret = IVAL(b,0) | (((int64)IVAL(b,4))<<32);
	}
#endif

	return ret;
}

void read_buf(int f,char *buf,int len)
{
  int ret;
  if ((ret=readfd(f,buf,len)) != len) {
    if (verbose > 1) 
      rprintf(FERROR,"(%d) Error reading %d bytes : %s\n",
	      getpid(),len,ret==-1?strerror(errno):"EOF");
    exit_cleanup(1);
  }
  total_read += len;
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


static char last_byte;
static int last_sparse;

int sparse_end(int f)
{
	if (last_sparse) {
		do_lseek(f,-1,SEEK_CUR);
		return (write(f,&last_byte,1) == 1 ? 0 : -1);
	}
	last_sparse = 0;
	return 0;
}


static int write_sparse(int f,char *buf,int len)
{
	int l1=0,l2=0;
	int ret;

	for (l1=0;l1<len && buf[l1]==0;l1++) ;
	for (l2=0;l2<(len-l1) && buf[len-(l2+1)]==0;l2++) ;

	last_byte = buf[len-1];

	if (l1 == len || l2 > 0)
		last_sparse=1;

	if (l1 > 0)
		do_lseek(f,l1,SEEK_CUR);  

	if (l1 == len) 
		return len;

	if ((ret=write(f,buf+l1,len-(l1+l2))) != len-(l1+l2)) {
		if (ret == -1 || ret == 0) return ret;
		return (l1+ret);
	}

	if (l2 > 0)
		do_lseek(f,l2,SEEK_CUR);
	
	return len;
}



int write_file(int f,char *buf,int len)
{
	int ret = 0;

	if (!sparse_files) 
		return write(f,buf,len);

	while (len>0) {
		int len1 = MIN(len, SPARSE_WRITE_SIZE);
		int r1 = write_sparse(f, buf, len1);
		if (r1 <= 0) {
			if (ret > 0) return ret;
			return r1;
		}
		len -= r1;
		buf += r1;
		ret += r1;
	}
	return ret;
}


static int writefd_unbuffered(int fd,char *buf,int len)
{
  int total = 0;
  fd_set w_fds, r_fds;
  int fd_count, count, got_select=0;
  struct timeval tv;

  if (buffer_f_in == -1) 
    return write(fd,buf,len);

  while (total < len) {
    int ret = write(fd,buf+total,len-total);

    if (ret == 0) return total;

    if (ret == -1 && !(errno == EWOULDBLOCK || errno == EAGAIN)) 
      return -1;

    if (ret == -1 && got_select) {
	    /* hmmm, we got a write select on the fd and then failed to write.
	       Why doesn't that mean that the fd is dead? It doesn't on some
	       systems it seems (eg. IRIX) */
	    u_sleep(1000);
#if 0
	    rprintf(FERROR,"write exception\n");
	    exit_cleanup(1);
#endif
    }

    got_select = 0;


    if (ret == -1) {
      read_check(buffer_f_in);

      fd_count = fd+1;
      FD_ZERO(&w_fds);
      FD_ZERO(&r_fds);
      FD_SET(fd,&w_fds);
      if (buffer_f_in != -1) {
	      FD_SET(buffer_f_in,&r_fds);
	      if (buffer_f_in > fd) 
		      fd_count = buffer_f_in+1;
      }

      tv.tv_sec = BLOCKING_TIMEOUT;
      tv.tv_usec = 0;
      count = select(fd_count,buffer_f_in == -1? NULL: &r_fds,
		     &w_fds,NULL,&tv);
      if (count == -1 && errno != EINTR) {
	      if (verbose > 1) 
		      rprintf(FERROR,"select error: %s\n", strerror(errno));
	      exit_cleanup(1);
      }

      if (count == 0) {
	      check_timeout();
	      continue;
      }
      
      if (FD_ISSET(fd, &w_fds)) {
	      got_select = 1;
      }
    } else {
      total += ret;
    }
  }

  if (io_timeout)
	  last_io = time(NULL);

  return total;
}

static char *io_buffer;
static int io_buffer_count;

void io_start_buffering(int fd)
{
	io_buffer = (char *)malloc(IO_BUFFER_SIZE);
	if (!io_buffer) out_of_memory("writefd");
	io_buffer_count = 0;
}

void io_end_buffering(int fd)
{
	if (io_buffer_count) {
		if (writefd_unbuffered(fd, io_buffer, 
				       io_buffer_count) != 
		    io_buffer_count) {
			rprintf(FERROR,"write failed\n");
			exit_cleanup(1);
		}
		io_buffer_count = 0;
	}
	free(io_buffer);
	io_buffer = NULL;
}

static int writefd(int fd,char *buf,int len1)
{
	int len = len1;

	if (!io_buffer) return writefd_unbuffered(fd, buf, len);

	while (len) {
		int n = MIN(len, IO_BUFFER_SIZE-io_buffer_count);
		if (n > 0) {
			memcpy(io_buffer+io_buffer_count, buf, n);
			buf += n;
			len -= n;
			io_buffer_count += n;
		}
		
		if (io_buffer_count == IO_BUFFER_SIZE) {
			if (writefd_unbuffered(fd, io_buffer, 
					       io_buffer_count) != 
			    io_buffer_count) {
				return -1;
			}
			io_buffer_count = 0;
		}
	}

	return len1;
}


void write_int(int f,int32 x)
{
  int ret;
  char b[4];
  SIVAL(b,0,x);
  if ((ret=writefd(f,b,4)) != 4) {
    rprintf(FERROR,"write_int failed : %s\n",
	    ret==-1?strerror(errno):"EOF");
    exit_cleanup(1);
  }
  total_written += 4;
}

void write_longint(int f, int64 x)
{
	extern int remote_version;
	char b[8];
	int ret;

	if (remote_version < 16 || x <= 0x7FFFFFFF) {
		write_int(f, (int)x);
		return;
	}

	write_int(f, -1);
	SIVAL(b,0,(x&0xFFFFFFFF));
	SIVAL(b,4,((x>>32)&0xFFFFFFFF));

	if ((ret=writefd(f,b,8)) != 8) {
		rprintf(FERROR,"write_longint failed : %s\n",
			ret==-1?strerror(errno):"EOF");
		exit_cleanup(1);
	}
	total_written += 8;
}

void write_buf(int f,char *buf,int len)
{
  int ret;
  if ((ret=writefd(f,buf,len)) != len) {
    rprintf(FERROR,"write_buf failed : %s\n",
	    ret==-1?strerror(errno):"EOF");
    exit_cleanup(1);
  }
  total_written += len;
}

/* write a string to the connection */
void write_sbuf(int f,char *buf)
{
	write_buf(f, buf, strlen(buf));
}


void write_byte(int f,unsigned char c)
{
	write_buf(f,(char *)&c,1);
}

void write_flush(int f)
{
}


int read_line(int f, char *buf, int maxlen)
{
	while (maxlen) {
		read_buf(f, buf, 1);
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
	len = vslprintf(buf, sizeof(buf)-1, format, ap);
	va_end(ap);

	if (len < 0) exit_cleanup(1);

	write_sbuf(fd, buf);
}

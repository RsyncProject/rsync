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

static int total_written = 0;
static int total_read = 0;

extern int verbose;
extern int sparse_files;

int write_total(void)
{
  return total_written;
}

int read_total(void)
{
  return total_read;
}

static int buffer_f_in = -1;

void setup_nonblocking(int f_in,int f_out)
{
  set_blocking(f_out,0);
  buffer_f_in = f_in;
}


static char *read_buffer = NULL;
static char *read_buffer_p = NULL;
static int read_buffer_len = 0;
static int read_buffer_size = 0;


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


static int readfd(int fd,char *buffer,int N)
{
  int  ret;
  int total=0;  

  if (read_buffer_len < N)
	  read_check(buffer_f_in);
 
  while (total < N)
    {
      if (read_buffer_len > 0 && buffer_f_in == fd) {
	ret = MIN(read_buffer_len,N-total);
	memcpy(buffer+total,read_buffer_p,ret);
	read_buffer_p += ret;
	read_buffer_len -= ret;
      } else {
	while ((ret = read(fd,buffer + total,N - total)) == -1) {
	  fd_set fds;

	  if (errno != EAGAIN && errno != EWOULDBLOCK)
	    return -1;
	  FD_ZERO(&fds);
	  FD_SET(fd, &fds);
	  select(fd+1, &fds, NULL, NULL, NULL);
	}
      }

      if (ret <= 0)
	return total;
      total += ret;
    }
  return total;
}


int read_int(int f)
{
  int ret;
  char b[4];
  if ((ret=readfd(f,b,4)) != 4) {
    if (verbose > 1) 
      fprintf(FERROR,"(%d) Error reading %d bytes : %s\n",
	      getpid(),4,ret==-1?strerror(errno):"EOF");
    exit_cleanup(1);
  }
  total_read += 4;
  return IVAL(b,0);
}

void read_buf(int f,char *buf,int len)
{
  int ret;
  if ((ret=readfd(f,buf,len)) != len) {
    if (verbose > 1) 
      fprintf(FERROR,"(%d) Error reading %d bytes : %s\n",
	      getpid(),len,ret==-1?strerror(errno):"EOF");
    exit_cleanup(1);
  }
  total_read += len;
}

unsigned char read_byte(int f)
{
  unsigned char c;
  read_buf(f,(char *)&c,1);
  return c;
}


static char last_byte=0;
static int last_sparse = 0;

int sparse_end(int f)
{
  if (last_sparse) {
    lseek(f,-1,SEEK_CUR);
    return (write(f,&last_byte,1) == 1 ? 0 : -1);
  }
  last_sparse = 0;
  return 0;
}

int write_sparse(int f,char *buf,int len)
{
  int l1=0,l2=0;
  int ret;

  if (!sparse_files) 
    return write(f,buf,len);

  for (l1=0;l1<len && buf[l1]==0;l1++) ;
  for (l2=0;l2<(len-l1) && buf[len-(l2+1)]==0;l2++) ;

  last_byte = buf[len-1];

  if (l1 == len || l2 > 0)
    last_sparse=1;

  if (l1 > 0)
    lseek(f,l1,SEEK_CUR);  

  if (l1 == len) 
    return len;

  if ((ret=write(f,buf+l1,len-(l1+l2))) != len-(l1+l2)) {
    if (ret == -1 || ret == 0) return ret;
    return (l1+ret);
  }

  if (l2 > 0)
    lseek(f,l2,SEEK_CUR);

  return len;
}

int read_write(int fd_in,int fd_out,int size)
{
  static char *buf=NULL;
  int bufsize = sparse_files?SPARSE_WRITE_SIZE:WRITE_SIZE;
  int total=0;
  
  if (!buf) {
    buf = (char *)malloc(bufsize);
    if (!buf) out_of_memory("read_write");
  }

  while (total < size) {
    int n = MIN(size-total,bufsize);
    read_buf(fd_in,buf,n);
    if (write_sparse(fd_out,buf,n) != n)
      return total;
    total += n;
  }
  return total;
}


static int writefd(int fd,char *buf,int len)
{
  int total = 0;
  fd_set w_fds, r_fds;
  int fd_count;
  struct timeval tv;

  if (buffer_f_in == -1) 
    return write(fd,buf,len);

  while (total < len) {
    int ret = write(fd,buf+total,len-total);

    if (ret == 0) return total;

    if (ret == -1 && !(errno == EWOULDBLOCK || errno == EAGAIN)) 
      return -1;

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
      select(fd_count,buffer_f_in == -1? NULL: &r_fds,&w_fds,NULL,&tv);
    } else {
      total += ret;
    }
  }

  return total;
}



void write_int(int f,int x)
{
  int ret;
  char b[4];
  SIVAL(b,0,x);
  if ((ret=writefd(f,b,4)) != 4) {
    fprintf(FERROR,"write_int failed : %s\n",
	    ret==-1?strerror(errno):"EOF");
    exit_cleanup(1);
  }
  total_written += 4;
}

void write_buf(int f,char *buf,int len)
{
  int ret;
  if ((ret=writefd(f,buf,len)) != len) {
    fprintf(FERROR,"write_buf failed : %s\n",
	    ret==-1?strerror(errno):"EOF");
    exit_cleanup(1);
  }
  total_written += len;
}


void write_byte(int f,unsigned char c)
{
  write_buf(f,(char *)&c,1);
}

void write_flush(int f)
{
}



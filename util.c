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

int write_total(void)
{
  return total_written;
}

int read_total(void)
{
  return total_read;
}

void write_int(int f,int x)
{
  char b[4];
  SIVAL(b,0,x);
  if (write(f,b,4) != 4) {
    fprintf(stderr,"write_int failed : %s\n",strerror(errno));
    exit(1);
  }
  total_written += 4;
}

void write_buf(int f,char *buf,int len)
{
  if (write(f,buf,len) != len) {
    fprintf(stderr,"write_buf failed : %s\n",strerror(errno));
    exit(1);
  }
  total_written += len;
}

static int num_waiting(int fd)
{
  int len=0;
#ifdef FIONREAD
  ioctl(fd,FIONREAD,&len);
#endif
  return(len);
}

static char *read_buffer = NULL;
static char *read_buffer_p = NULL;
static int read_buffer_len = 0;
static int read_buffer_size = 0;


void write_flush(int f)
{
}


/* This function was added to overcome a deadlock problem when using
 * ssh.  It looks like we can't allow our receive queue to get full or
 * ssh will clag up. Uggh.  */
void read_check(int f)
{
  int n;

  if (read_buffer_len == 0) {
    read_buffer_p = read_buffer;
  }

  if ((n=num_waiting(f)) <= 0)
    return;

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
 
  while (total < N)
    {
      if (read_buffer_len > 0) {
	ret = MIN(read_buffer_len,N-total);
	memcpy(buffer+total,read_buffer_p,ret);
	read_buffer_p += ret;
	read_buffer_len -= ret;
      } else {
	ret = read(fd,buffer + total,N - total);
      }

      if (ret <= 0)
	return total;
      total += ret;
    }
  return total;
}


int read_int(int f)
{
  char b[4];
  if (readfd(f,b,4) != 4) {
    if (verbose > 1) 
      fprintf(stderr,"Error reading %d bytes : %s\n",4,strerror(errno));
    exit(1);
  }
  total_read += 4;
  return IVAL(b,0);
}

void read_buf(int f,char *buf,int len)
{
  if (readfd(f,buf,len) != len) {
    if (verbose > 1) 
      fprintf(stderr,"Error reading %d bytes : %s\n",len,strerror(errno));
    exit(1);
  }
  total_read += len;
}


char *map_file(int fd,off_t len)
{
  char *ret = (char *)mmap(NULL,len,PROT_READ,MAP_SHARED,fd,0);
  return ret;
}

void unmap_file(char *buf,off_t len)
{
  if (len > 0 && buf)
    munmap(buf,len);
}


int read_write(int fd_in,int fd_out,int size)
{
  static char *buf=NULL;
  static int bufsize = WRITE_BLOCK_SIZE;
  int total=0;
  
  if (!buf) {
    buf = (char *)malloc(bufsize);
    if (!buf) out_of_memory("read_write");
  }

  while (total < size) {
    int n = MIN(size-total,bufsize);
    read_buf(fd_in,buf,n);
    if (write(fd_out,buf,n) != n)
      return total;
    total += n;
  }
  return total;
}


/* this is taken from CVS */
int piped_child(char **command,int *f_in,int *f_out)
{
  int pid;
  int to_child_pipe[2];
  int from_child_pipe[2];

  if (pipe(to_child_pipe) < 0 ||
      pipe(from_child_pipe) < 0) {
    fprintf(stderr,"pipe: %s\n",strerror(errno));
    exit(1);
  }


  pid = fork();
  if (pid < 0) {
    fprintf(stderr,"fork: %s\n",strerror(errno));
    exit(1);
  }

  if (pid == 0)
    {
      if (dup2(to_child_pipe[0], STDIN_FILENO) < 0 ||
	  close(to_child_pipe[1]) < 0 ||
	  close(from_child_pipe[0]) < 0 ||
	  dup2(from_child_pipe[1], STDOUT_FILENO) < 0) {
	fprintf(stderr,"Failed to dup/close : %s\n",strerror(errno));
	exit(1);
      }
      execvp(command[0], command);
      fprintf(stderr,"Failed to exec %s : %s\n",
	      command[0],strerror(errno));
      exit(1);
    }

  if (close(from_child_pipe[1]) < 0 ||
      close(to_child_pipe[0]) < 0) {
    fprintf(stderr,"Failed to close : %s\n",strerror(errno));   
    exit(1);
  }

  *f_in = from_child_pipe[0];
  *f_out = to_child_pipe[1];
  
  return pid;
}


void out_of_memory(char *str)
{
  fprintf(stderr,"out of memory in %s\n",str);
  exit(1);
}


#ifndef HAVE_STRDUP
 char *strdup(char *s)
{
  int l = strlen(s) + 1;
  char *ret = (char *)malloc(l);
  if (ret)
    strcpy(ret,s);
  return ret;
}
#endif


int set_modtime(char *fname,time_t modtime)
{
#ifdef HAVE_UTIME_H
  struct utimbuf tbuf;  
  tbuf.actime = time(NULL);
  tbuf.modtime = modtime;
  return utime(fname,&tbuf);
#elif defined(HAVE_UTIME)
  time_t t[2];
  t[0] = time(NULL);
  t[1] = modtime;
  return utime(fname,t);
#else
  struct timeval t[2];
  t[0].tv_sec = time(NULL);
  t[0].tv_usec = 0;
  t[1].tv_sec = modtime;
  t[1].tv_usec = 0;
  return utimes(fname,t);
#endif
}


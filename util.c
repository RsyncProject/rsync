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

int num_waiting(int fd)
{
  int len=0;
  ioctl(fd,FIONREAD,&len);
  return(len);
}

static int map_fd = -1;
static off_t map_size = 0;
static char *p = NULL;
static int p_size = 0;
static int p_offset = 0;
static int p_len = 0;


char *map_file(int fd,off_t len)
{
  char *ret = NULL;
#ifdef HAVE_MMAP
  if (len < MAX_MAP_SIZE)
    ret = (char *)mmap(NULL,len,PROT_READ,MAP_SHARED,fd,0);
#endif
  map_fd = fd;
  map_size = len; 
  return ret;
}

char *map_ptr(char *buf,off_t offset,int len)
{
  if (buf)
    return buf+offset;

  if (len == 0) 
    return NULL;

  len = MIN(len,map_size-offset);

  if (offset >= p_offset && 
      offset+len <= p_offset+p_len) {
    return (p + (offset - p_offset));
  }

  len = MAX(len,CHUNK_SIZE);
  len = MIN(len,map_size - offset);  

  if (len > p_size) {
    if (p) free(p);
    p = (char *)malloc(len);
    if (!p) out_of_memory("map_ptr");
    p_size = len;
  }

  if (lseek(map_fd,offset,SEEK_SET) != offset ||
      read(map_fd,p,len) != len) {
    fprintf(FERROR,"EOF in map_ptr!\n");
    exit_cleanup(1);
  }

  p_offset = offset;
  p_len = len;

  return p; 
}


void unmap_file(char *buf,off_t len)
{
#ifdef HAVE_MMAP
  if (len > 0 && buf)
    munmap(buf,len);
#endif
  map_fd = -1;
  map_size = 0;
  p_len = 0;
}


/* this is taken from CVS */
int piped_child(char **command,int *f_in,int *f_out)
{
  int pid;
  int to_child_pipe[2];
  int from_child_pipe[2];

  if (pipe(to_child_pipe) < 0 ||
      pipe(from_child_pipe) < 0) {
    fprintf(FERROR,"pipe: %s\n",strerror(errno));
    exit_cleanup(1);
  }


  pid = fork();
  if (pid < 0) {
    fprintf(FERROR,"fork: %s\n",strerror(errno));
    exit_cleanup(1);
  }

  if (pid == 0)
    {
      if (dup2(to_child_pipe[0], STDIN_FILENO) < 0 ||
	  close(to_child_pipe[1]) < 0 ||
	  close(from_child_pipe[0]) < 0 ||
	  dup2(from_child_pipe[1], STDOUT_FILENO) < 0) {
	fprintf(FERROR,"Failed to dup/close : %s\n",strerror(errno));
	exit_cleanup(1);
      }
      execvp(command[0], command);
      fprintf(FERROR,"Failed to exec %s : %s\n",
	      command[0],strerror(errno));
      exit_cleanup(1);
    }

  if (close(from_child_pipe[1]) < 0 ||
      close(to_child_pipe[0]) < 0) {
    fprintf(FERROR,"Failed to close : %s\n",strerror(errno));   
    exit_cleanup(1);
  }

  *f_in = from_child_pipe[0];
  *f_out = to_child_pipe[1];
  
  return pid;
}


void out_of_memory(char *str)
{
  fprintf(FERROR,"out of memory in %s\n",str);
  exit_cleanup(1);
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



/****************************************************************************
Set a fd into blocking/nonblocking mode. Uses POSIX O_NONBLOCK if available,
else
if SYSV use O_NDELAY
if BSD use FNDELAY
****************************************************************************/
int set_blocking(int fd, int set)
{
  int val;
#ifdef O_NONBLOCK
#define FLAG_TO_SET O_NONBLOCK
#else
#ifdef SYSV
#define FLAG_TO_SET O_NDELAY
#else /* BSD */
#define FLAG_TO_SET FNDELAY
#endif
#endif

  if((val = fcntl(fd, F_GETFL, 0)) == -1)
	return -1;
  if(set) /* Turn blocking on - ie. clear nonblock flag */
	val &= ~FLAG_TO_SET;
  else
    val |= FLAG_TO_SET;
  return fcntl( fd, F_SETFL, val);
#undef FLAG_TO_SET
}

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

#define BLOCK_SIZE 700
#define RSYNC_RSH_ENV "RSYNC_RSH"

#define RSYNC_NAME "rsync"
#define BACKUP_SUFFIX "~"

/* a non-zero CHAR_OFFSET makes the rolling sum stronger, but is
   imcompatible with older versions :-( */
#define CHAR_OFFSET 0


#define FILE_VALID 1
#define SAME_MODE (1<<1)
#define SAME_RDEV (1<<2)
#define SAME_UID (1<<3)
#define SAME_GID (1<<4)
#define SAME_DIR (1<<5)
#define SAME_NAME SAME_DIR
#define LONG_NAME (1<<6)
#define SAME_TIME (1<<7)

/* update this if you make incompatible changes */
#define PROTOCOL_VERSION 14
#define MIN_PROTOCOL_VERSION 10
#define MAX_PROTOCOL_VERSION 20

#define SPARSE_WRITE_SIZE (4*1024)
#define WRITE_SIZE (32*1024)
#define CHUNK_SIZE (32*1024)
#define MAX_MAP_SIZE (4*1024*1024)

#define BLOCKING_TIMEOUT 10

#define FERROR stderr
#define FINFO (am_server?stderr:stdout)

#include "config.h"

#if HAVE_REMSH
#define RSYNC_RSH "remsh"
#else
#define RSYNC_RSH "rsh"
#endif

#include <sys/types.h>
#ifdef HAVE_UNISTD_H
#include <unistd.h>
#endif
#include <stdio.h>

#ifdef HAVE_SYS_PARAM_H
#include <sys/param.h>
#endif

#ifdef HAVE_STDLIB_H
#include <stdlib.h>
#endif

#ifdef HAVE_STRING_H
#include <string.h>
#endif

#ifdef HAVE_COMPAT_H
#include <compat.h>
#endif

#ifdef HAVE_MALLOC_H
#include <malloc.h>
#endif

#ifdef TIME_WITH_SYS_TIME
#include <sys/time.h>
#include <time.h>
#else
#ifdef HAVE_SYS_TIME_H
#include <sys/time.h>
#else
#include <time.h>
#endif
#endif

#ifdef HAVE_FCNTL_H
#include <fcntl.h>
#else
#ifdef HAVE_SYS_FCNTL_H
#include <sys/fcntl.h>
#endif
#endif

#include <sys/stat.h>

#ifdef HAVE_SYS_IOCTL_H
#include <sys/ioctl.h>
#endif

#ifdef HAVE_SYS_FILIO_H
#include <sys/filio.h>
#endif

#include <signal.h>
#ifdef HAVE_SYS_WAIT_H
#include <sys/wait.h>
#endif
#ifdef HAVE_CTYPE_H
#include <ctype.h>
#endif
#ifdef HAVE_GRP_H
#include <grp.h>
#endif
#include <errno.h>

#ifdef HAVE_MMAP
#include <sys/mman.h>
#endif

#ifdef HAVE_UTIME_H
#include <utime.h>
#endif

#ifdef HAVE_SYS_SELECT_H
#include <sys/select.h>
#endif

#ifdef HAVE_FNMATCH
#include <fnmatch.h>
#else
#include "lib/fnmatch.h"
#endif

#ifdef HAVE_GETOPT_LONG
#include <getopt.h>
#else
#include "lib/getopt.h"
#endif


#ifndef S_ISLNK
#define S_ISLNK(mode) (((mode) & S_IFLNK) == S_IFLNK)
#endif

#ifndef uchar
#define uchar unsigned char
#endif

#ifndef int32
#if (SIZEOF_INT == 4)
#define int32 int
#elif (SIZEOF_LONG == 4)
#define int32 long
#elif (SIZEOF_SHORT == 4)
#define int32 short
#endif
#endif

#ifndef uint32
#define uint32 unsigned int32
#endif


#ifndef MIN
#define MIN(a,b) ((a)<(b)?(a):(b))
#endif

#ifndef MAX
#define MAX(a,b) ((a)>(b)?(a):(b))
#endif

/* the length of the md4 checksum */
#define MD4_SUM_LENGTH 16
#define SUM_LENGTH 16

#ifndef MAXPATHLEN
#define MAXPATHLEN 1024
#endif

struct file_struct {
  time_t modtime;
  off_t length;
  mode_t mode;
  ino_t inode;
  dev_t dev;
  dev_t rdev;
  uid_t uid;
  gid_t gid;
  char *name;
  char *dir;
  char *link;
  char sum[MD4_SUM_LENGTH];
};

struct file_list {
  int count;
  int malloced;
  struct file_struct *files;
};

struct sum_buf {
  off_t offset;			/* offset in file of this chunk */
  int len;			/* length of chunk of file */
  int i;			/* index of this chunk */
  uint32 sum1;	                /* simple checksum */
  char sum2[SUM_LENGTH];	/* checksum  */
};

struct sum_struct {
  off_t flength;		/* total file length */
  int count;			/* how many chunks */
  int remainder;		/* flength % block_length */
  int n;			/* block_length */
  struct sum_buf *sums;		/* points to info for each chunk */
};

struct map_struct {
  char *map,*p;
  int fd,size,p_size,p_offset,p_len;
};

#include "byteorder.h"
#include "version.h"
#include "proto.h"
#include "md4.h"

#if !HAVE_STRERROR
extern char *sys_errlist[];
#define strerror(i) sys_errlist[i]
#endif

#ifndef HAVE_STRCHR
# define strchr                 index
# define strrchr                rindex
#endif

#if HAVE_DIRENT_H
# include <dirent.h>
#else
# define dirent direct
# if HAVE_SYS_NDIR_H
#  include <sys/ndir.h>
# endif
# if HAVE_SYS_DIR_H
#  include <sys/dir.h>
# endif
# if HAVE_NDIR_H
#  include <ndir.h>
# endif
#endif

#ifndef HAVE_ERRNO_DECL
extern int errno;
#endif

#ifndef HAVE_BCOPY
#define bcopy(src,dest,n) memcpy(dest,src,n)
#endif

#ifndef HAVE_BZERO
#define bzero(buf,n) memset(buf,0,n)
#endif

#define SUPPORT_LINKS (HAVE_READLINK && defined(S_ISLNK))
#define SUPPORT_HARD_LINKS HAVE_LINK

#ifndef S_ISLNK
#define S_ISLNK(x) 0
#endif

#if !SUPPORT_LINKS
#define lstat stat
#endif

#ifndef HAVE_LCHOWN
#define lchown chown
#endif

#define SIGNAL_CAST (RETSIGTYPE (*)())

#ifndef EWOULDBLOCK
#define EWOULDBLOCK EAGAIN
#endif

#ifndef STDIN_FILENO
#define STDIN_FILENO 0
#endif

#ifndef STDOUT_FILENO
#define STDOUT_FILENO 1
#endif

#ifndef STDERR_FILENO
#define STDERR_FILENO 2
#endif

#ifndef S_IWUSR
#define S_IWUSR 0200
#endif

#define IS_DEVICE(mode) (S_ISCHR(mode) || S_ISBLK(mode))


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

#define False 0
#define True 1

#define BLOCK_SIZE 700
#define RSYNC_RSH_ENV "RSYNC_RSH"

#define RSYNC_NAME "rsync"
#define RSYNCD_CONF "/etc/rsyncd.conf"

#define DEFAULT_LOCK_FILE "/var/run/rsyncd.lock"
#define URL_PREFIX "rsync://"

#define BACKUP_SUFFIX "~"

/* a non-zero CHAR_OFFSET makes the rolling sum stronger, but is
   incompatible with older versions :-( */
#define CHAR_OFFSET 0


#define FLAG_DELETE (1<<0)
#define SAME_MODE (1<<1)
#define SAME_RDEV (1<<2)
#define SAME_UID (1<<3)
#define SAME_GID (1<<4)
#define SAME_DIR (1<<5)
#define SAME_NAME SAME_DIR
#define LONG_NAME (1<<6)
#define SAME_TIME (1<<7)

/* update this if you make incompatible changes */
#define PROTOCOL_VERSION 20
#define MIN_PROTOCOL_VERSION 11
#define MAX_PROTOCOL_VERSION 30

#define RSYNC_PORT 873

#define SPARSE_WRITE_SIZE (1024)
#define WRITE_SIZE (32*1024)
#define CHUNK_SIZE (32*1024)
#define MAX_MAP_SIZE (256*1024)
#define IO_BUFFER_SIZE (4092)
#define MAX_READ_BUFFER (1024*1024)

#define MAX_ARGS 1000

#define MPLEX_BASE 7
#define FERROR 1
#define FINFO 2
#define FLOG 3

#include "errcode.h"

#include "config.h"

#if HAVE_REMSH
#define RSYNC_RSH "remsh"
#else
#define RSYNC_RSH "rsh"
#endif

#include <sys/types.h>

#ifdef HAVE_GETOPT_LONG
#include <getopt.h>
#else
#include "lib/getopt.h"
#endif

#ifdef HAVE_UNISTD_H
#include <unistd.h>
#endif
#include <stdio.h>
#include <stddef.h>

#ifdef HAVE_SYS_PARAM_H
#include <sys/param.h>
#endif

#ifdef HAVE_STDLIB_H
#include <stdlib.h>
#endif

#ifdef HAVE_SYS_SOCKET_H
#include <sys/socket.h>
#endif

#ifdef HAVE_STRING_H
#include <string.h>
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

#ifdef HAVE_UTIME_H
#include <utime.h>
#endif

#ifdef HAVE_SYS_SELECT_H
#include <sys/select.h>
#endif

#ifdef HAVE_SYS_MODE_H
/* apparently AIX needs this for S_ISLNK */
#ifndef S_ISLNK
#include <sys/mode.h>
#endif
#endif

#ifdef HAVE_FNMATCH
#include <fnmatch.h>
#else
#include "lib/fnmatch.h"
#endif

#ifdef HAVE_GLOB_H
#include <glob.h>
#endif

/* these are needed for the uid/gid mapping code */
#include <pwd.h>
#include <grp.h>

#include <stdarg.h>
#include <netinet/in.h>
#include <arpa/inet.h>
#include <netdb.h>
#include <syslog.h>
#include <sys/file.h>

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

#ifdef HAVE_COMPAT_H
#include <compat.h>
#endif


#define BOOL int

#ifndef uchar
#define uchar unsigned char
#endif

#if HAVE_UNSIGNED_CHAR
#define schar signed char
#else
#define schar char
#endif

#ifndef int32
#if (SIZEOF_INT == 4)
#define int32 int
#elif (SIZEOF_LONG == 4)
#define int32 long
#elif (SIZEOF_SHORT == 4)
#define int32 short
#else
/* I hope this works */
#define int32 int
#define LARGE_INT32
#endif
#endif

#ifndef uint32
#define uint32 unsigned int32
#endif

#if HAVE_OFF64_T
#define OFF_T off64_t
#define STRUCT_STAT struct stat64
#else
#define OFF_T off_t
#define STRUCT_STAT struct stat
#endif

#if HAVE_OFF64_T
#define int64 off64_t
#elif (SIZEOF_LONG == 8) 
#define int64 long
#elif (SIZEOF_INT == 8) 
#define int64 int
#elif HAVE_LONGLONG
#define int64 long long
#else
#define int64 off_t
#define NO_INT64
#endif

#if HAVE_SHORT_INO_T
#define INO_T uint32
#else
#define INO_T ino_t
#endif

#ifndef MIN
#define MIN(a,b) ((a)<(b)?(a):(b))
#endif

#ifndef MAX
#define MAX(a,b) ((a)>(b)?(a):(b))
#endif

#ifndef MAXHOSTNAMELEN
#define MAXHOSTNAMELEN 256
#endif

/* the length of the md4 checksum */
#define MD4_SUM_LENGTH 16
#define SUM_LENGTH 16

#ifndef MAXPATHLEN
#define MAXPATHLEN 1024
#endif

#ifndef INADDR_NONE
#define INADDR_NONE 0xffffffff
#endif

struct file_struct {
	unsigned flags;
	time_t modtime;
	OFF_T length;
	mode_t mode;
	INO_T inode;
	dev_t dev;
	dev_t rdev;
	uid_t uid;
	gid_t gid;
	char *basename;
	char *dirname;
	char *basedir;
	char *link;
	char *sum;
};


struct file_list {
	int count;
	int malloced;
	struct file_struct **files;
};

struct sum_buf {
	OFF_T offset;		/* offset in file of this chunk */
	int len;		/* length of chunk of file */
	int i;			/* index of this chunk */
	uint32 sum1;	        /* simple checksum */
	char sum2[SUM_LENGTH];	/* checksum  */
};

struct sum_struct {
  OFF_T flength;		/* total file length */
  int count;			/* how many chunks */
  int remainder;		/* flength % block_length */
  int n;			/* block_length */
  struct sum_buf *sums;		/* points to info for each chunk */
};

struct map_struct {
	char *p;
	int fd,p_size,p_len;
	OFF_T file_size, p_offset, p_fd_offset;
};

struct exclude_struct {
	char *orig;
	char *pattern;
	int regular_exp;
	int fnmatch_flags;
	int include;
	int directory;
	int local;
};

struct stats {
	int64 total_size;
	int64 total_transferred_size;
	int64 total_written;
	int64 total_read;
	int64 literal_data;
	int64 matched_data;
	int flist_size;
	int num_files;
	int num_transferred_files;
};


/* we need this function because of the silly way in which duplicate
   entries are handled in the file lists - we can't change this
   without breaking existing versions */
static inline int flist_up(struct file_list *flist, int i)
{
	while (!flist->files[i]->basename) i++;
	return i;
}

#include "byteorder.h"
#include "version.h"
#include "proto.h"
#include "lib/mdfour.h"

#if !HAVE_STRERROR
extern char *sys_errlist[];
#define strerror(i) sys_errlist[i]
#endif

#ifndef HAVE_STRCHR
# define strchr                 index
# define strrchr                rindex
#endif

#ifndef HAVE_ERRNO_DECL
extern int errno;
#endif

#define SUPPORT_LINKS HAVE_READLINK
#define SUPPORT_HARD_LINKS HAVE_LINK

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

#ifndef _S_IFMT
#define _S_IFMT        0170000
#endif

#ifndef _S_IFLNK
#define _S_IFLNK  0120000
#endif

#ifndef S_ISLNK
#define S_ISLNK(mode) (((mode) & (_S_IFMT)) == (_S_IFLNK))
#endif

#ifndef S_ISBLK
#define S_ISBLK(mode) (((mode) & (_S_IFMT)) == (_S_IFBLK))
#endif

#ifndef S_ISCHR
#define S_ISCHR(mode) (((mode) & (_S_IFMT)) == (_S_IFCHR))
#endif

#ifndef S_ISSOCK
#ifdef _S_IFSOCK
#define S_ISSOCK(mode) (((mode) & (_S_IFMT)) == (_S_IFSOCK))
#else
#define S_ISSOCK(mode) (0)
#endif
#endif

#ifndef S_ISFIFO
#ifdef _S_IFIFO
#define S_ISFIFO(mode) (((mode) & (_S_IFMT)) == (_S_IFIFO))
#else
#define S_ISFIFO(mode) (0)
#endif
#endif

#ifndef S_ISDIR
#define S_ISDIR(mode) (((mode) & (_S_IFMT)) == (_S_IFDIR))
#endif

#ifndef S_ISREG
#define S_ISREG(mode) (((mode) & (_S_IFMT)) == (_S_IFREG))
#endif


#define IS_DEVICE(mode) (S_ISCHR(mode) || S_ISBLK(mode) || S_ISSOCK(mode) || S_ISFIFO(mode))

#ifndef ACCESSPERMS
#define ACCESSPERMS 0777
#endif
/* Initial mask on permissions given to temporary files.  Mask off setuid
     bits and group access because of potential race-condition security
     holes, and mask other access because mode 707 is bizarre */
#define INITACCESSPERMS 0700

/* handler for null strings in printf format */
#define NS(s) ((s)?(s):"<NULL>")

/* use magic gcc attributes to catch format errors */
 void rprintf(int , const char *, ...)
#ifdef __GNUC__
     __attribute__ ((format (printf, 2, 3)))
#endif
;

#ifdef REPLACE_INET_NTOA
#define inet_ntoa rep_inet_ntoa
#endif


#ifndef HAVE_STRLCPY
size_t strlcpy(char *d, const char *s, size_t bufsize);
#endif

#ifndef HAVE_STRLCAT
size_t strlcat(char *d, const char *s, size_t bufsize);
#endif

#define exit_cleanup(code) _exit_cleanup(code, __FILE__, __LINE__)

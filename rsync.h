/* 
   Copyright (C) by Andrew Tridgell 1996, 2000
   Copyright (C) Paul Mackerras 1996
   Copyright (C) 2001, 2002 by Martin Pool <mbp@samba.org>
   
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
#define RSYNCD_SYSCONF "/etc/rsyncd.conf"
#define RSYNCD_USERCONF "rsyncd.conf"

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
#define PROTOCOL_VERSION 26

/* We refuse to interoperate with versions that are not in this range.
 * Note that we assume we'll work with later versions: the onus is on
 * people writing them to make sure that they don't send us anything
 * we won't understand.
 *
 * There are two possible explanations for the limit at thirty: either
 * to allow new major-rev versions that do not interoperate with us,
 * and (more likely) so that we can detect an attempt to connect rsync
 * to a non-rsync server, which is unlikely to begin by sending a byte
 * between 15 and 30. */
#define MIN_PROTOCOL_VERSION 15
#define MAX_PROTOCOL_VERSION 30

#define RSYNC_PORT 873

#define SPARSE_WRITE_SIZE (1024)
#define WRITE_SIZE (32*1024)
#define CHUNK_SIZE (32*1024)
#define MAX_MAP_SIZE (256*1024)
#define IO_BUFFER_SIZE (4092)

#define MAX_ARGS 1000

#define MPLEX_BASE 7

/* Log values.  I *think* what these mean is: FLOG goes to the server
 * logfile; FERROR and FINFO try to end up on the client, with
 * different levels of filtering. */
enum logcode {FNONE=0, FERROR=1, FINFO=2, FLOG=3 };

#include "errcode.h"

#include "config.h"

/* The default RSYNC_RSH is always set in config.h, either to "remsh",
 * "rsh", or otherwise something specified by the user.  HAVE_REMSH
 * controls parameter munging for HP/UX, etc. */

#include <sys/types.h>

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

#ifdef HAVE_MALLOC_H
#  include <malloc.h>
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

#include <assert.h>


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
/* As long as it gets... */
#define int64 off_t
#define NO_INT64
#endif

/* Starting from protocol version 26, we always use 64-bit
 * ino_t and dev_t internally, even if this platform does not
 * allow files to have 64-bit inums.  That's because the
 * receiver needs to find duplicate (dev,ino) tuples to detect
 * hardlinks, and it might have files coming from a platform
 * that has 64-bit inums.
 *
 * The only exception is if we're on a platform with no 64-bit type at
 * all.
 *
 * Because we use read_longint() to get these off the wire, if you
 * transfer devices or hardlinks with dev or inum > 2**32 to a machine
 * with no 64-bit types then you will get an overflow error.  Probably
 * not many people have that combination of machines, and you can
 * avoid it by not preserving hardlinks or not transferring device
 * nodes.  It's not clear that any other behaviour is better.
 *
 * Note that if you transfer devices from a 64-bit-devt machine (say,
 * Solaris) to a 32-bit-devt machine (say, Linux-2.2/x86) then the
 * device numbers will be truncated.  But it's a kind of silly thing
 * to do anyhow.
 *
 * FIXME: In future, we should probable split the device number into
 * major/minor, and transfer the two parts as 32-bit ints.  That gives
 * you somewhat more of a chance that they'll come from a big machine
 * to a little one in a useful way.
 *
 * FIXME: Really we need an unsigned type, and we perhaps ought to
 * cope with platforms on which this is an unsigned int or even a
 * struct.  Later.
 */ 
#define INO64_T int64
#define DEV64_T int64

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

#ifndef IN_LOOPBACKNET
#define IN_LOOPBACKNET 127
#endif

struct file_struct {
	unsigned flags;
	time_t modtime;
	OFF_T length;
	mode_t mode;

	INO64_T inode;
	/** Device this file lives upon */
	DEV64_T dev;

	/** If this is a device node, the device number. */
	DEV64_T rdev;
	uid_t uid;
	gid_t gid;
	char *basename;
	char *dirname;
	char *basedir;
	char *link;
	char *sum;
};


#define ARENA_SIZE	(32 * 1024)

struct string_area {
	char *base;
	char *end;
	char *current;
	struct string_area *next;
};

struct file_list {
	int count;
	int malloced;
	struct file_struct **files;
	struct string_area *string_area;
};

struct sum_buf {
	OFF_T offset;		/**< offset in file of this chunk */
	int len;		/**< length of chunk of file */
	int i;			/**< index of this chunk */
	uint32 sum1;	        /**< simple checksum */
	char sum2[SUM_LENGTH];	/**< checksum  */
};

struct sum_struct {
	OFF_T flength;		/**< total file length */
	size_t count;		/**< how many chunks */
	size_t remainder;	/**< flength % block_length */
	size_t n;		/**< block_length */
	struct sum_buf *sums;	/**< points to info for each chunk */
};

struct map_struct {
	char *p;
	int fd,p_size,p_len;
	OFF_T file_size, p_offset, p_fd_offset;
};

struct exclude_struct {
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
#include "lib/mdfour.h"
#include "lib/permstring.h"
#include "lib/addrinfo.h"

#include "proto.h"

/* We have replacement versions of these if they're missing. */
#ifndef HAVE_ASPRINTF
int asprintf(char **ptr, const char *format, ...);
#endif

#ifndef HAVE_VASPRINTF
int vasprintf(char **ptr, const char *format, va_list ap);
#endif

#if !defined(HAVE_VSNPRINTF) && !defined(HAVE_C99_VSNPRINTF)
int vsnprintf (char *str, size_t count, const char *fmt, va_list args);
#endif

#if !defined(HAVE_SNPRINTF) && !defined(HAVE_C99_VSNPRINTF)
int snprintf(char *str,size_t count,const char *fmt,...);
#endif


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

/* This could be bad on systems which have no lchown and where chown
 * follows symbollic links.  On such systems it might be better not to
 * try to chown symlinks at all. */
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

/* work out what fcntl flag to use for non-blocking */
#ifdef O_NONBLOCK
# define NONBLOCK_FLAG O_NONBLOCK
#elif defined(SYSV)
# define NONBLOCK_FLAG O_NDELAY
#else 
# define NONBLOCK_FLAG FNDELAY
#endif

#ifndef INADDR_LOOPBACK
#define INADDR_LOOPBACK 0x7f000001
#endif

#ifndef INADDR_NONE
#define INADDR_NONE 0xffffffff
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

#if !defined(__GNUC__) || defined(APPLE)
/* Apparently the OS X port of gcc gags on __attribute__.
 *
 * <http://www.opensource.apple.com/bugs/X/gcc/2512150.html> */
#define __attribute__(x) 

#endif

/* Convenient wrappers for malloc and realloc.  Use them. */
#define new(type) ((type *)malloc(sizeof(type)))
#define new_array(type, num) ((type *)_new_array(sizeof(type), (num)))
#define realloc_array(ptr, type, num) ((type *)_realloc_array((ptr), sizeof(type), (num)))

/* use magic gcc attributes to catch format errors */
 void rprintf(enum logcode , const char *, ...)
     __attribute__((format (printf, 2, 3)))
;

/* This is just like rprintf, but it also tries to print some
 * representation of the error code.  Normally errcode = errno. */
void rsyserr(enum logcode, int, const char *, ...)
     __attribute__((format (printf, 3, 4)))
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

#ifndef WEXITSTATUS
#define	WEXITSTATUS(stat)	((int)(((stat)>>8)&0xFF))
#endif

#define exit_cleanup(code) _exit_cleanup(code, __FILE__, __LINE__)


extern int verbose;

#ifndef HAVE_INET_NTOP
const char *                 
inet_ntop(int af, const void *src, char *dst, size_t size);
#endif /* !HAVE_INET_NTOP */

#ifndef HAVE_INET_PTON
int inet_pton(int af, const char *src, void *dst);
#endif

#ifdef MAINTAINER_MODE
const char *get_panic_action(void);
#endif

#define UNUSED(x) x __attribute__((__unused__))

extern const char *io_write_phase, *io_read_phase;

/*
 * Copyright (C) 1996, 2000 Andrew Tridgell
 * Copyright (C) 1996 Paul Mackerras
 * Copyright (C) 2001, 2002 Martin Pool <mbp@samba.org>
 * Copyright (C) 2003, 2004, 2005, 2006 Wayne Davison
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
 * You should have received a copy of the GNU General Public License along
 * with this program; if not, write to the Free Software Foundation, Inc.,
 * 51 Franklin Street - Fifth Floor, Boston, MA 02110-1301, USA.
 */

#define False 0
#define True 1

#define BLOCK_SIZE 700
#define RSYNC_RSH_ENV "RSYNC_RSH"
#define RSYNC_RSH_IO_ENV "RSYNC_RSH_IO"

#define RSYNC_NAME "rsync"
/* RSYNCD_SYSCONF is now set in config.h */
#define RSYNCD_USERCONF "rsyncd.conf"

#define DEFAULT_LOCK_FILE "/var/run/rsyncd.lock"
#define URL_PREFIX "rsync://"

#define BACKUP_SUFFIX "~"

/* a non-zero CHAR_OFFSET makes the rolling sum stronger, but is
   incompatible with older versions :-( */
#define CHAR_OFFSET 0

/* These flags are only used during the flist transfer. */

#define XMIT_TOP_DIR (1<<0)
#define XMIT_SAME_MODE (1<<1)
#define XMIT_EXTENDED_FLAGS (1<<2)
#define XMIT_SAME_RDEV_pre28 XMIT_EXTENDED_FLAGS /* Only in protocols < 28 */
#define XMIT_SAME_UID (1<<3)
#define XMIT_SAME_GID (1<<4)
#define XMIT_SAME_NAME (1<<5)
#define XMIT_LONG_NAME (1<<6)
#define XMIT_SAME_TIME (1<<7)
#define XMIT_SAME_RDEV_MAJOR (1<<8)
#define XMIT_HAS_IDEV_DATA (1<<9)
#define XMIT_SAME_DEV (1<<10)
#define XMIT_RDEV_MINOR_IS_SMALL (1<<11)

/* These flags are used in the live flist data. */

#define FLAG_TOP_DIR (1<<0)
#define FLAG_SENT (1<<1)	/* sender */
#define FLAG_HLINK_EOL (1<<1)	/* receiver/generator */
#define FLAG_MOUNT_POINT (1<<2)	/* sender/generator */
#define FLAG_DEL_HERE (1<<3)	/* receiver/generator */
#define FLAG_HLINK_TOL (1<<4)	/* receiver/generator */
#define FLAG_NO_FUZZY (1<<5)	/* generator */
#define FLAG_MISSING (1<<6)	/* generator */

/* update this if you make incompatible changes */
#define PROTOCOL_VERSION 29

/* We refuse to interoperate with versions that are not in this range.
 * Note that we assume we'll work with later versions: the onus is on
 * people writing them to make sure that they don't send us anything
 * we won't understand.
 *
 * Interoperation with old but supported protocol versions
 * should cause a warning to be printed.  At a future date
 * the old protocol will become the minimum and
 * compatibility code removed.
 *
 * There are two possible explanations for the limit at
 * MAX_PROTOCOL_VERSION: either to allow new major-rev versions that
 * do not interoperate with us, and (more likely) so that we can
 * detect an attempt to connect rsync to a non-rsync server, which is
 * unlikely to begin by sending a byte between MIN_PROTOCL_VERSION and
 * MAX_PROTOCOL_VERSION. */

#define MIN_PROTOCOL_VERSION 20
#define OLD_PROTOCOL_VERSION 25
#define MAX_PROTOCOL_VERSION 40

#define RSYNC_PORT 873

#define SPARSE_WRITE_SIZE (1024)
#define WRITE_SIZE (32*1024)
#define CHUNK_SIZE (32*1024)
#define MAX_MAP_SIZE (256*1024)
#define IO_BUFFER_SIZE (4092)
#define MAX_BLOCK_SIZE ((int32)1 << 29)

#define IOERR_GENERAL	(1<<0) /* For backward compatibility, this must == 1 */
#define IOERR_VANISHED	(1<<1)
#define IOERR_DEL_LIMIT (1<<2)

#define MAX_ARGS 1000
#define MAX_BASIS_DIRS 20
#define MAX_SERVER_ARGS (MAX_BASIS_DIRS*2 + 100)

#define MPLEX_BASE 7

#define NO_FILTERS	0
#define SERVER_FILTERS	1
#define ALL_FILTERS	2

#define XFLG_FATAL_ERRORS	(1<<0)
#define XFLG_OLD_PREFIXES	(1<<1)
#define XFLG_ANCHORED2ABS	(1<<2)
#define XFLG_ABS_IF_SLASH	(1<<3)

#define ATTRS_REPORT		(1<<0)
#define ATTRS_SKIP_MTIME	(1<<1)

#define FULL_FLUSH	1
#define NORMAL_FLUSH	0

#define PDIR_CREATE	1
#define PDIR_DELETE	0

/* Note: 0x00 - 0x7F are used for basis_dir[] indexes! */
#define FNAMECMP_BASIS_DIR_LOW	0x00 /* Must remain 0! */
#define FNAMECMP_BASIS_DIR_HIGH 0x7F
#define FNAMECMP_FNAME		0x80
#define FNAMECMP_PARTIAL_DIR	0x81
#define FNAMECMP_BACKUP 	0x82
#define FNAMECMP_FUZZY		0x83

/* For use by the itemize_changes code */
#define ITEM_REPORT_ATIME (1<<0)
#define ITEM_REPORT_CHECKSUM (1<<1)
#define ITEM_REPORT_SIZE (1<<2)
#define ITEM_REPORT_TIME (1<<3)
#define ITEM_REPORT_PERMS (1<<4)
#define ITEM_REPORT_OWNER (1<<5)
#define ITEM_REPORT_GROUP (1<<6)
#define ITEM_REPORT_ACL (1<<7)
#define ITEM_REPORT_XATTR (1<<8)
#define ITEM_BASIS_TYPE_FOLLOWS (1<<11)
#define ITEM_XNAME_FOLLOWS (1<<12)
#define ITEM_IS_NEW (1<<13)
#define ITEM_LOCAL_CHANGE (1<<14)
#define ITEM_TRANSFER (1<<15)
/* These are outside the range of the transmitted flags. */
#define ITEM_MISSING_DATA (1<<16)	   /* used by log_formatted() */
#define ITEM_DELETED (1<<17)		   /* used by log_formatted() */

#define SIGNIFICANT_ITEM_FLAGS (~(\
	ITEM_BASIS_TYPE_FOLLOWS | ITEM_XNAME_FOLLOWS | ITEM_LOCAL_CHANGE))


/* Log-message categories.  Only FERROR and FINFO get sent over the socket,
 * but FLOG and FSOCKERR can be sent over the receiver -> generator pipe.
 * FLOG only goes to the log file, not the client; FCLIENT is the opposite. */
enum logcode { FNONE=0, FERROR=1, FINFO=2, FLOG=3, FCLIENT=4, FSOCKERR=5 };

/* Messages types that are sent over the message channel.  The logcode
 * values must all be present here with identical numbers. */
enum msgcode {
	MSG_DATA=0,	/* raw data on the multiplexed stream */
	MSG_ERROR=FERROR, MSG_INFO=FINFO, /* remote logging */
	MSG_LOG=FLOG, MSG_SOCKERR=FSOCKERR, /* sibling logging */
	MSG_REDO=9,	/* reprocess indicated flist index */
	MSG_SUCCESS=100,/* successfully updated indicated flist index */
	MSG_DELETED=101,/* successfully deleted a file on receiving side */
	MSG_DONE=86	/* current phase is done */
};

#include "errcode.h"

#include "config.h"

/* The default RSYNC_RSH is always set in config.h. */

#include <stdio.h>
#ifdef HAVE_SYS_TYPES_H
# include <sys/types.h>
#endif
#ifdef HAVE_SYS_STAT_H
# include <sys/stat.h>
#endif
#ifdef STDC_HEADERS
# include <stdlib.h>
# include <stddef.h>
#else
# ifdef HAVE_STDLIB_H
#  include <stdlib.h>
# endif
#endif
#ifdef HAVE_STRING_H
# if !defined STDC_HEADERS && defined HAVE_MEMORY_H
#  include <memory.h>
# endif
# include <string.h>
#endif
#ifdef HAVE_STRINGS_H
# include <strings.h>
#endif
#ifdef HAVE_UNISTD_H
# include <unistd.h>
#endif

#ifdef HAVE_SYS_PARAM_H
#include <sys/param.h>
#endif

#if defined HAVE_MALLOC_H && (defined HAVE_MALLINFO || !defined HAVE_STDLIB_H)
#include <malloc.h>
#endif

#ifdef HAVE_SYS_SOCKET_H
#include <sys/socket.h>
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

#ifdef HAVE_DIRENT_H
# include <dirent.h>
#else
# define dirent direct
# ifdef HAVE_SYS_NDIR_H
#  include <sys/ndir.h>
# endif
# ifdef HAVE_SYS_DIR_H
#  include <sys/dir.h>
# endif
# ifdef HAVE_NDIR_H
#  include <ndir.h>
# endif
#endif

#ifdef MAJOR_IN_MKDEV
#include <sys/mkdev.h>
# if !defined makedev && (defined mkdev || defined _WIN32 || defined __WIN32__)
#  define makedev mkdev
# endif
#elif defined MAJOR_IN_SYSMACROS
#include <sys/sysmacros.h>
#endif

#ifdef MAKEDEV_TAKES_3_ARGS
#define MAKEDEV(devmajor,devminor) makedev(0,devmajor,devminor)
#else
#define MAKEDEV(devmajor,devminor) makedev(devmajor,devminor)
#endif

#ifdef HAVE_COMPAT_H
#include <compat.h>
#endif

#ifdef HAVE_LIMITS_H
# include <limits.h>
#endif

#include <assert.h>

#include "lib/pool_alloc.h"

#define BOOL int

#ifndef uchar
#define uchar unsigned char
#endif

#ifdef SIGNED_CHAR_OK
#define schar signed char
#else
#define schar char
#endif

/* Find a variable that is either exactly 32-bits or longer.
 * If some code depends on 32-bit truncation, it will need to
 * take special action in a "#if SIZEOF_INT32 > 4" section. */
#ifndef int32
#if SIZEOF_INT == 4
# define int32 int
# define SIZEOF_INT32 4
#elif SIZEOF_LONG == 4
# define int32 long
# define SIZEOF_INT32 4
#elif SIZEOF_SHORT == 4
# define int32 short
# define SIZEOF_INT32 4
#elif SIZEOF_INT > 4
# define int32 int
# define SIZEOF_INT32 SIZEOF_INT
#elif SIZEOF_LONG > 4
# define int32 long
# define SIZEOF_INT32 SIZEOF_LONG
#else
# error Could not find a 32-bit integer variable
#endif
#else
# define SIZEOF_INT32 4
#endif

#ifndef uint32
#define uint32 unsigned int32
#endif

#if SIZEOF_OFF_T == 8 || !SIZEOF_OFF64_T || !defined HAVE_STRUCT_STAT64
#define OFF_T off_t
#define STRUCT_STAT struct stat
#else
#define OFF_T off64_t
#define STRUCT_STAT struct stat64
#define USE_STAT64_FUNCS 1
#endif

/* CAVEAT: on some systems, int64 will really be a 32-bit integer IFF
 * that's the maximum size the file system can handle and there is no
 * 64-bit type available.  The rsync source must therefore take steps
 * to ensure that any code that really requires a 64-bit integer has
 * it (e.g. the checksum code uses two 32-bit integers for its 64-bit
 * counter). */
#if SIZEOF_LONG == 8
# define int64 long
# define SIZEOF_INT64 8
#elif SIZEOF_INT == 8
# define int64 int
# define SIZEOF_INT64 8
#elif SIZEOF_LONG_LONG == 8
# define int64 long long
# define SIZEOF_INT64 8
#elif SIZEOF_OFF64_T == 8
# define int64 off64_t
# define SIZEOF_INT64 8
#elif SIZEOF_OFF_T == 8
# define int64 off_t
# define SIZEOF_INT64 8
#elif SIZEOF_INT > 8
# define int64 int
# define SIZEOF_INT64 SIZEOF_INT
#elif SIZEOF_LONG > 8
# define int64 long
# define SIZEOF_INT64 SIZEOF_LONG
#elif SIZEOF_LONG_LONG > 8
# define int64 long long
# define SIZEOF_INT64 SIZEOF_LONG_LONG
#else
/* As long as it gets... */
# define int64 off_t
# define SIZEOF_INT64 SIZEOF_OFF_T
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
 * FIXME: I don't think the code in flist.c has ever worked on a system
 * where dev_t is a struct.
 */

struct idev {
	int64 inode;
	int64 dev;
};

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
#define SHORT_SUM_LENGTH 2
#define BLOCKSUM_BIAS 10

#ifndef MAXPATHLEN
#define MAXPATHLEN 1024
#endif

/* We want a roomy line buffer that can hold more than MAXPATHLEN,
 * and significantly more than an overly short MAXPATHLEN. */
#if MAXPATHLEN < 4096
#define BIGPATHBUFLEN (4096+1024)
#else
#define BIGPATHBUFLEN (MAXPATHLEN+1024)
#endif

#ifndef NAME_MAX
#define NAME_MAX 255
#endif

#ifndef INADDR_NONE
#define INADDR_NONE 0xffffffff
#endif

#ifndef IN_LOOPBACKNET
#define IN_LOOPBACKNET 127
#endif

#define GID_NONE ((gid_t)-1)

#define HL_CHECK_MASTER	0
#define HL_SKIP		1

struct hlink {
	int32 next;
	int32 hlindex;
	unsigned short link_dest_used;
};

#define F_DEV	link_u.idev->dev
#define F_INODE	link_u.idev->inode

#define F_HLINDEX link_u.links->hlindex
#define F_NEXT	link_u.links->next

struct file_struct {
	union {
		dev_t rdev;	/* The device number, if this is a device */
		char *sum;	/* Only a normal file can have a checksum */
		char *link;	/* Points to symlink string, if a symlink */
	} u;
	OFF_T length;
	char *basename;		/* The current item's name (AKA filename) */
	char *dirname;		/* The directory info inside the transfer */
	union {
		char *root;	/* Sender-side dir info outside transfer */
		int depth;	/* Receiver-side directory depth info */
	} dir;
	union {
		struct idev *idev;
		struct hlink *links;
	} link_u;
	time_t modtime;
	uid_t uid;
	gid_t gid;
	mode_t mode;
	uchar flags;	/* this item MUST remain last */
};

/*
 * Start the flist array at FLIST_START entries and grow it
 * by doubling until FLIST_LINEAR then grow by FLIST_LINEAR
 */
#define FLIST_START	(32 * 1024)
#define FLIST_LINEAR	(FLIST_START * 512)

/*
 * Extent size for allocation pools A minimum size of 128KB
 * is needed to mmap them so that freeing will release the
 * space to the OS.
 *
 * Larger sizes reduce leftover fragments and speed free calls
 * (when they happen) Smaller sizes increase the chance of
 * freed allocations freeing whole extents.
 */
#define FILE_EXTENT	(256 * 1024)
#define HLINK_EXTENT	(128 * 1024)

#define WITH_HLINK	1
#define WITHOUT_HLINK	0

struct file_list {
	struct file_struct **files;
	alloc_pool_t file_pool;
	alloc_pool_t hlink_pool;
	int count;
	int malloced;
	int low, high;
};

#define SUMFLG_SAME_OFFSET	(1<<0)

struct sum_buf {
	OFF_T offset;		/**< offset in file of this chunk */
	int32 len;		/**< length of chunk of file */
	uint32 sum1;	        /**< simple checksum */
	int32 chain;		/**< next hash-table collision */
	short flags;		/**< flag bits */
	char sum2[SUM_LENGTH];	/**< checksum  */
};

struct sum_struct {
	OFF_T flength;		/**< total file length */
	struct sum_buf *sums;	/**< points to info for each chunk */
	int32 count;		/**< how many chunks */
	int32 blength;		/**< block_length */
	int32 remainder;	/**< flength % block_length */
	int s2length;		/**< sum2_length */
};

struct map_struct {
	OFF_T file_size;	/* File size (from stat)		*/
	OFF_T p_offset;		/* Window start				*/
	OFF_T p_fd_offset;	/* offset of cursor in fd ala lseek	*/
	char *p;		/* Window pointer			*/
	int32 p_size;		/* Largest window size we allocated	*/
	int32 p_len;		/* Latest (rounded) window size		*/
	int32 def_window_size;	/* Default window size			*/
	int fd;			/* File Descriptor			*/
	int status;		/* first errno from read errors		*/
};

#define MATCHFLG_WILD		(1<<0) /* pattern has '*', '[', and/or '?' */
#define MATCHFLG_WILD2		(1<<1) /* pattern has '**' */
#define MATCHFLG_WILD2_PREFIX	(1<<2) /* pattern starts with "**" */
#define MATCHFLG_WILD3_SUFFIX	(1<<3) /* pattern ends with "***" */
#define MATCHFLG_ABS_PATH	(1<<4) /* path-match on absolute path */
#define MATCHFLG_INCLUDE	(1<<5) /* this is an include, not an exclude */
#define MATCHFLG_DIRECTORY	(1<<6) /* this matches only directories */
#define MATCHFLG_WORD_SPLIT	(1<<7) /* split rules on whitespace */
#define MATCHFLG_NO_INHERIT	(1<<8) /* don't inherit these rules */
#define MATCHFLG_NO_PREFIXES	(1<<9) /* parse no prefixes from patterns */
#define MATCHFLG_MERGE_FILE	(1<<10)/* specifies a file to merge */
#define MATCHFLG_PERDIR_MERGE	(1<<11)/* merge-file is searched per-dir */
#define MATCHFLG_EXCLUDE_SELF	(1<<12)/* merge-file name should be excluded */
#define MATCHFLG_FINISH_SETUP	(1<<13)/* per-dir merge file needs setup */
#define MATCHFLG_NEGATE 	(1<<14)/* rule matches when pattern does not */
#define MATCHFLG_CVS_IGNORE	(1<<15)/* rule was -C or :C */
#define MATCHFLG_SENDER_SIDE	(1<<16)/* rule applies to the sending side */
#define MATCHFLG_RECEIVER_SIDE	(1<<17)/* rule applies to the receiving side */
#define MATCHFLG_CLEAR_LIST 	(1<<18)/* this item is the "!" token */

#define MATCHFLGS_FROM_CONTAINER (MATCHFLG_ABS_PATH | MATCHFLG_INCLUDE \
				| MATCHFLG_DIRECTORY | MATCHFLG_SENDER_SIDE \
				| MATCHFLG_NEGATE | MATCHFLG_RECEIVER_SIDE)

struct filter_struct {
	struct filter_struct *next;
	char *pattern;
	uint32 match_flags;
	union {
		int slash_cnt;
		struct filter_list_struct *mergelist;
	} u;
};

struct filter_list_struct {
	struct filter_struct *head;
	struct filter_struct *tail;
	char *debug_type;
};

struct stats {
	int64 total_size;
	int64 total_transferred_size;
	int64 total_written;
	int64 total_read;
	int64 literal_data;
	int64 matched_data;
	int64 flist_buildtime;
	int64 flist_xfertime;
	int flist_size;
	int num_files;
	int num_transferred_files;
	int current_file_index;
};

struct chmod_mode_struct;

#include "byteorder.h"
#include "lib/mdfour.h"
#include "lib/wildmatch.h"
#include "lib/permstring.h"
#include "lib/addrinfo.h"

#if !defined __GNUC__ || defined __APPLE__
/* Apparently the OS X port of gcc gags on __attribute__.
 *
 * <http://www.opensource.apple.com/bugs/X/gcc/2512150.html> */
#define __attribute__(x)
#endif

#define UNUSED(x) x __attribute__((__unused__))
#define NORETURN __attribute__((__noreturn__))

#include "proto.h"

/* We have replacement versions of these if they're missing. */
#ifndef HAVE_ASPRINTF
int asprintf(char **ptr, const char *format, ...);
#endif

#ifndef HAVE_VASPRINTF
int vasprintf(char **ptr, const char *format, va_list ap);
#endif

#if !defined HAVE_VSNPRINTF || !defined HAVE_C99_VSNPRINTF
#define vsnprintf rsync_vsnprintf
int vsnprintf(char *str, size_t count, const char *fmt, va_list args);
#endif

#if !defined HAVE_SNPRINTF || !defined HAVE_C99_VSNPRINTF
#define snprintf rsync_snprintf
int snprintf(char *str,size_t count,const char *fmt,...);
#endif


#ifndef HAVE_STRERROR
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

#ifdef HAVE_READLINK
#define SUPPORT_LINKS 1
#endif
#ifdef HAVE_LINK
#define SUPPORT_HARD_LINKS 1
#endif

#ifdef HAVE_SIGACTION
#define SIGACTION(n,h) sigact.sa_handler=(h), sigaction((n),&sigact,NULL)
#define signal(n,h) we_need_to_call_SIGACTION_not_signal(n,h)
#else
#define SIGACTION(n,h) signal(n,h)
#endif

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

#ifndef S_IRUSR
#define S_IRUSR 0400
#endif

#ifndef S_IWUSR
#define S_IWUSR 0200
#endif

#ifndef ACCESSPERMS
#define ACCESSPERMS 0777
#endif

#ifndef S_ISVTX
#define S_ISVTX 0
#endif

#define CHMOD_BITS (S_ISUID | S_ISGID | S_ISVTX | ACCESSPERMS)

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
#elif defined SYSV
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

#define IS_SPECIAL(mode) (S_ISSOCK(mode) || S_ISFIFO(mode))
#define IS_DEVICE(mode) (S_ISCHR(mode) || S_ISBLK(mode))

/* Initial mask on permissions given to temporary files.  Mask off setuid
     bits and group access because of potential race-condition security
     holes, and mask other access because mode 707 is bizarre */
#define INITACCESSPERMS 0700

/* handler for null strings in printf format */
#define NS(s) ((s)?(s):"<NULL>")

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

/* Make sure that the O_BINARY flag is defined. */
#ifndef O_BINARY
#define O_BINARY 0
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
#ifndef WIFEXITED
#define	WIFEXITED(stat)		((int)((stat)&0xFF) == 0)
#endif

#define exit_cleanup(code) _exit_cleanup(code, __FILE__, __LINE__)

#ifdef HAVE_GETEUID
#define MY_UID() geteuid()
#else
#define MY_UID() getuid()
#endif

#ifdef HAVE_GETEGID
#define MY_GID() getegid()
#else
#define MY_GID() getgid()
#endif

extern int verbose;

#ifndef HAVE_INET_NTOP
const char *inet_ntop(int af, const void *src, char *dst, size_t size);
#endif

#ifndef HAVE_INET_PTON
int inet_pton(int af, const char *src, void *dst);
#endif

#ifdef MAINTAINER_MODE
const char *get_panic_action(void);
#endif

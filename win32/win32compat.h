/*
 * POSIX compatibility shim for building rsync with MSVC/clang-cl on Windows.
 *
 * config.h includes this before rsync.h starts pulling in system headers, so
 * everything declared here is visible to the whole tree.  The strategy is:
 *
 *   - Let the CRT handle real files and pipes through its own int fds.
 *   - Give sockets their own pseudo-fd range (WIN32_SOCK_BASE and up) with a
 *     side table, since Winsock SOCKETs are not CRT fds.
 *   - Route the handful of POSIX calls rsync makes through win32_* wrappers
 *     that dispatch on which kind of fd they were handed.
 *
 * Copyright (C) 2026 Max Vilimpoc, rsync CMake/Windows port.
 * Distributed under the same GPL-3.0-or-later terms as the rest of rsync.
 */

#ifndef RSYNC_WIN32COMPAT_H
#define RSYNC_WIN32COMPAT_H

#ifndef _WIN32
#error "win32compat.h is only for Windows builds"
#endif

#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#ifndef NOMINMAX
#define NOMINMAX
#endif
#ifndef _CRT_SECURE_NO_WARNINGS
#define _CRT_SECURE_NO_WARNINGS
#endif
#ifndef _CRT_NONSTDC_NO_WARNINGS
#define _CRT_NONSTDC_NO_WARNINGS
#endif

#include <winsock2.h>
#include <ws2tcpip.h>
#include <windows.h>

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>

#ifdef __MINGW32__
# include <io.h>
#else
# include <corecrt_io.h>
#endif
#include <direct.h>
#include <process.h>
#include <sys/types.h>
#include <sys/stat.h>
#include <sys/time.h>
#include <sys/utime.h>
#include <fcntl.h>
#include <time.h>
#include <signal.h>

#undef ERROR
#undef DELETE
#undef IN
#undef OUT
#undef small

#ifdef __GNUC__
#define RSYNC_TLS __thread
#else
#define RSYNC_TLS __declspec(thread)
#endif

#define IS_ABS_PATH(p) ((p)[0] == '/' \
			|| (isalpha((uchar)(p)[0]) && (p)[1] == ':' \
			    && ((p)[2] == '/' || (p)[2] == '\\')))

#define IS_DRIVE_PATH(s) (isalpha((uchar)(s)[0]) && (s)[1] == ':' \
			  && ((s)[2] == '\\' || (s)[2] == '/' || (s)[2] == '\0'))

#define CHDIR_VIA_DIRFD 0

#ifdef __GNUC__
# define NORETURN __attribute__((noreturn))
#else
# define NORETURN __declspec(noreturn)
#endif

#define platform_init()                   win32_init()
#define platform_fix_path_args(argc, argv) win32_fix_path_args((argc), (argv))

#define close_sibling_fd(fd) ((void)0)

void win32_init(void);
void win32_harden(void);
void win32_fix_path_args(int argc, char *argv[]);

typedef int             uid_t;
typedef int             gid_t;
typedef int             id_t;
typedef short           nlink_t;
#ifndef __MINGW32__
typedef unsigned short  mode_t;
typedef int             pid_t;
typedef __int64         off64_t;
#endif
typedef __int64         blksize_t;
typedef __int64         blkcnt_t;

#ifndef _SSIZE_T_DEFINED
#define _SSIZE_T_DEFINED
#ifdef _WIN64
typedef __int64         ssize_t;
#else
typedef int             ssize_t;
#endif
#endif

struct win32_stat {
	dev_t            st_dev;
	unsigned __int64 st_ino;
	mode_t           st_mode;
	nlink_t          st_nlink;
	uid_t            st_uid;
	gid_t            st_gid;
	dev_t            st_rdev;
	__int64          st_size;
	__time64_t       st_atime;
	__time64_t       st_mtime;
	__time64_t       st_ctime;
};

#define STRUCT_STAT           struct win32_stat
#define OFF_T                 __int64
#define SIZEOF_CAPITAL_OFF_T  8
#define USE_STAT64_FUNCS      1

int win32_stat(const char *path, struct win32_stat *st);
int win32_lstat(const char *path, struct win32_stat *st);
int win32_fstat(int fd, struct win32_stat *st);

#define WIN32_LINK_SYMLINK  0
#define WIN32_LINK_HARDLINK 1

int  win32_oserr_to_errno(unsigned long err);
char *win32_strerror(int err);
int  win32_attrs_are_symlink(unsigned long attrs, unsigned long tag);
void win32_links_init(void);
void win32_note_link(const char *path, int kind);

#define stat64(p, b)    win32_stat((p), (b))
#define lstat64(p, b)   win32_lstat((p), (b))
#define fstat(f, b)     win32_fstat((f), (b))
#define fstat64(f, b)   win32_fstat((f), (b))
#define lseek64         _lseeki64
#define ftruncate64     win32_ftruncate64

#define ftruncate(f, n) win32_ftruncate64((f), (n))

#ifndef S_IFLNK
#define S_IFLNK  0xA000
#endif
#ifndef S_IFSOCK
#define S_IFSOCK 0xC000
#endif
#ifndef S_IFBLK
#define S_IFBLK  0x6000
#endif

#ifndef S_ISREG
#define S_ISREG(m)  (((m) & _S_IFMT) == _S_IFREG)
#endif
#ifndef S_ISDIR
#define S_ISDIR(m)  (((m) & _S_IFMT) == _S_IFDIR)
#endif
#ifndef S_ISCHR
#define S_ISCHR(m)  (((m) & _S_IFMT) == _S_IFCHR)
#endif
#ifndef S_ISFIFO
#define S_ISFIFO(m) (((m) & _S_IFMT) == _S_IFIFO)
#endif
#ifndef S_ISLNK
#define S_ISLNK(m)  (((m) & _S_IFMT) == S_IFLNK)
#endif
#ifndef S_ISBLK
#define S_ISBLK(m)  (((m) & _S_IFMT) == S_IFBLK)
#endif
#ifndef S_ISSOCK
#define S_ISSOCK(m) (((m) & _S_IFMT) == S_IFSOCK)
#endif

#ifndef S_IRUSR
#define S_IRUSR 0400
#define S_IWUSR 0200
#define S_IXUSR 0100
#endif
#ifndef S_IRWXU
#define S_IRWXU 0700
#endif
#ifndef S_IRGRP
#define S_IRGRP 0040
#endif
#ifndef S_IWGRP
#define S_IWGRP 0020
#endif
#ifndef S_IXGRP
#define S_IXGRP 0010
#endif
#ifndef S_IRWXG
#define S_IRWXG 0070
#endif
#ifndef S_IROTH
#define S_IROTH 0004
#endif
#ifndef S_IWOTH
#define S_IWOTH 0002
#endif
#ifndef S_IXOTH
#define S_IXOTH 0001
#endif
#ifndef S_IRWXO
#define S_IRWXO 0007
#endif
#ifndef S_ISUID
#define S_ISUID 04000
#endif
#ifndef S_ISGID
#define S_ISGID 02000
#endif
#ifndef S_ISVTX
#define S_ISVTX 01000
#endif
#ifndef ACCESSPERMS
#define ACCESSPERMS 0777
#endif
#ifndef S_BLKSIZE
#define S_BLKSIZE 512
#endif

#ifndef AT_REMOVEDIR
#define AT_REMOVEDIR 0x200
#endif

#ifndef O_NOFOLLOW
#define O_NOFOLLOW  0
#endif
#ifndef O_DIRECTORY
#define O_DIRECTORY 0
#endif
#ifndef O_CLOEXEC
#define O_CLOEXEC   0
#endif
#ifndef O_NOATIME
#define O_NOATIME   0
#endif

#ifndef O_NOCTTY
#define O_NOCTTY    0
#endif
#ifndef O_NONBLOCK
#define O_NONBLOCK  0x40000000
#endif

#define open            win32_open
int win32_open(const char *path, int flags, ...);

#ifndef F_OK
#define F_OK 0
#define X_OK 0
#define W_OK 2
#define R_OK 4
#endif
#define access(p, m)    _access((p), (m))

#define WIN32_SOCK_BASE  0x40000000

int      win32_is_sockfd(int fd);
SOCKET   win32_sockfd_handle(int fd);
int      win32_sockfd_alloc(SOCKET s);

int      win32_read(int fd, void *buf, unsigned int count);
int      win32_write(int fd, const void *buf, unsigned int count);
int      win32_close(int fd);
int      win32_pipe(int fd[2]);
int      win32_socketpair(int domain, int type, int protocol, int sv[2]);
int      win32_symlink(const char *target, const char *linkpath);
int      win32_select(int nfds, fd_set *rfds, fd_set *wfds, fd_set *efds,
                      struct timeval *tv);
int      win32_fcntl(int fd, int cmd, ...);
int      win32_dup(int fd);
int      win32_dup2(int oldfd, int newfd);

#define read(f, b, n)   win32_read((f), (b), (unsigned int)(n))
#define write(f, b, n)  win32_write((f), (b), (unsigned int)(n))
#define close(f)        win32_close(f)
#define pipe(a)         win32_pipe(a)
#define socketpair(d, t, p, sv) win32_socketpair((d), (t), (p), (sv))
#define select(n, r, w, e, t)   win32_select((n), (r), (w), (e), (t))
#define symlink(t, l)           win32_symlink((t), (l))
#define fcntl           win32_fcntl
#define dup(f)          win32_dup(f)
#define dup2(o, n)      win32_dup2((o), (n))

int win32_socket(int af, int type, int protocol);
int win32_accept(int fd, struct sockaddr *addr, socklen_t *addrlen);
int win32_bind(int fd, const struct sockaddr *addr, socklen_t addrlen);
int win32_connect(int fd, const struct sockaddr *addr, socklen_t addrlen);
int win32_listen(int fd, int backlog);
int win32_setsockopt(int fd, int level, int opt, const void *val, socklen_t len);
int win32_getsockopt(int fd, int level, int opt, void *val, socklen_t *len);
int win32_getpeername(int fd, struct sockaddr *addr, socklen_t *len);
int win32_getsockname(int fd, struct sockaddr *addr, socklen_t *len);
int win32_shutdown(int fd, int how);
int win32_recv(int fd, void *buf, int len, int flags);
int win32_send(int fd, const void *buf, int len, int flags);

#define socket(a, t, p)         win32_socket((a), (t), (p))
#define accept(f, a, l)         win32_accept((f), (a), (l))
#define bind(f, a, l)           win32_bind((f), (a), (l))
#define connect(f, a, l)        win32_connect((f), (a), (l))
#define listen(f, b)            win32_listen((f), (b))
#define setsockopt(f, l, o, v, n) win32_setsockopt((f), (l), (o), (v), (n))
#define getsockopt(f, l, o, v, n) win32_getsockopt((f), (l), (o), (v), (n))
#define getpeername(f, a, l)    win32_getpeername((f), (a), (l))
#define getsockname(f, a, l)    win32_getsockname((f), (a), (l))
#define shutdown(f, h)          win32_shutdown((f), (h))

#define F_GETFL 3
#define F_SETFL 4
#define F_GETFD 1
#define F_SETFD 2
#define F_SETLK 6
#define FD_CLOEXEC 1

struct flock {
    short l_type;
    short l_whence;
    off64_t l_start;
    off64_t l_len;
    pid_t l_pid;
};
#define F_RDLCK 0
#define F_WRLCK 1
#define F_UNLCK 2

int  win32_chown(const char *path, uid_t uid, gid_t gid);
int  win32_chmod(const char *path, mode_t mode);
int  win32_mkdir(const char *path, mode_t mode);
int  win32_rename(const char *from, const char *to);
int  win32_unlink(const char *path);
int  win32_mkstemp64(char *tmpl);
int  win32_utimes(const char *path, const struct timeval tv[2]);
int  win32_ftruncate64(int fd, off64_t length);
int  win32_fsync(int fd);

#define chown(p, u, g)      win32_chown((p), (u), (g))
#define lchown(p, u, g)     win32_chown((p), (u), (g))
#define chmod(p, m)         win32_chmod((p), (m))
#define mkdir(p, m)         win32_mkdir((p), (m))
#define rename(f, t)        win32_rename((f), (t))
#define unlink(p)           win32_unlink(p)
#define mkstemp64(t)        win32_mkstemp64(t)
#define utimes(p, t)        win32_utimes((p), (t))
#define fsync(f)            win32_fsync(f)
#define strerror(e)         win32_strerror(e)

#define open64              win32_open
#define lchmod              win32_chmod

#ifndef NAME_MAX
#define NAME_MAX 255
#endif
#ifndef MAXPATHLEN
#define MAXPATHLEN 4096
#endif
#ifndef PATH_MAX
#define PATH_MAX 4096
#endif
#ifndef MAXHOSTNAMELEN
#define MAXHOSTNAMELEN 256
#endif
#ifndef HOST_NAME_MAX
#define HOST_NAME_MAX 255
#endif

struct dirent {
    unsigned __int64 d_ino;
    unsigned char    d_type;
    char             d_name[NAME_MAX + 1];
};

#define DT_UNKNOWN 0
#define DT_DIR     4
#define DT_REG     8
#define DT_LNK    10

typedef struct DIR DIR;

DIR           *win32_opendir(const char *path);
struct dirent *win32_readdir(DIR *dirp);
int            win32_closedir(DIR *dirp);

#define opendir(p)  win32_opendir(p)
#define readdir(d)  win32_readdir(d)
#define closedir(d) win32_closedir(d)

struct passwd {
    char  *pw_name;
    char  *pw_passwd;
    uid_t  pw_uid;
    gid_t  pw_gid;
    char  *pw_gecos;
    char  *pw_dir;
    char  *pw_shell;
};

struct group {
    char  *gr_name;
    char  *gr_passwd;
    gid_t  gr_gid;
    char **gr_mem;
};

struct passwd *win32_getpwuid(uid_t uid);
struct passwd *win32_getpwnam(const char *name);
struct group  *win32_getgrgid(gid_t gid);
struct group  *win32_getgrnam(const char *name);

#define getpwuid(u) win32_getpwuid(u)
#define getpwnam(n) win32_getpwnam(n)
#define getgrgid(g) win32_getgrgid(g)
#define getgrnam(n) win32_getgrnam(n)
#define endpwent()  ((void)0)
#define endgrent()  ((void)0)

#define WIN32_FAKE_UID 1000
#define WIN32_FAKE_GID 1000
#define getuid()    (WIN32_FAKE_UID)
#define geteuid()   (WIN32_FAKE_UID)
#define getgid()    (WIN32_FAKE_GID)
#define getegid()   (WIN32_FAKE_GID)
#define setuid(u)   (0)
#define seteuid(u)  (0)
#define setgid(g)   (0)
#define setegid(g)  (0)
#define umask(m)    (0)
#define getpid()    ((pid_t)GetCurrentProcessId())

pid_t win32_piped_child(char **command, int *f_in, int *f_out);

pid_t win32_fork_thread(void (*fn)(void *), void *arg);
void  win32_remember_thread_child(pid_t pid, HANDLE h);
pid_t win32_waitpid(pid_t pid, int *status, int options);
int   win32_kill(pid_t pid, int sig);

#define waitpid(p, s, o) win32_waitpid((p), (s), (o))
#define kill(p, s)       win32_kill((p), (s))

#ifndef WNOHANG
#define WNOHANG 1
#endif
#define WIFEXITED(s)    (((s) & 0x7f) == 0)
#define WEXITSTATUS(s)  (((s) >> 8) & 0xff)
#define WIFSIGNALED(s)  (((s) & 0x7f) != 0 && ((s) & 0x7f) != 0x7f)
#define WTERMSIG(s)     ((s) & 0x7f)
#define WIFSTOPPED(s)   (0)

typedef void (*win32_sighandler_t)(int);
win32_sighandler_t win32_signal(int sig, win32_sighandler_t handler);
#define signal(n, h) win32_signal((n), (win32_sighandler_t)(h))

#ifndef SIGUSR1
#define SIGUSR1 30
#endif
#ifndef SIGUSR2
#define SIGUSR2 31
#endif
#ifndef SIGHUP
#define SIGHUP  1
#endif
#ifndef SIGPIPE
#define SIGPIPE 13
#endif
#ifndef SIGALRM
#define SIGALRM 14
#endif
#ifndef SIGCHLD
#define SIGCHLD 17
#endif
#ifndef SIGCONT
#define SIGCONT 18
#endif

char *win32_getpass(const char *prompt);
#define getpass(p) win32_getpass(p)

int  win32_gettimeofday(struct timeval *tv, void *tz);
unsigned int win32_sleep(unsigned int seconds);
int  win32_usleep(unsigned int usec);
int  win32_gethostname(char *name, size_t len);

#define gettimeofday(t, z) win32_gettimeofday((t), (z))
#define sleep(s)           win32_sleep(s)
#define usleep(u)          win32_usleep(u)
#define gethostname(n, l)  win32_gethostname((n), (l))

#define strcasecmp  _stricmp
#define strncasecmp _strnicmp
#define strdup      _strdup
#define isatty      _isatty
#define getcwd      _getcwd
#define chdir       _chdir
#define rmdir       _rmdir
#define alarm(s)    (0)
#define setsid()    (0)
#define fork()      (win32_no_fork())
#define execvp(f, a) (win32_no_fork())
#define nice(n)     (0)

int win32_no_fork(void);

#ifndef EWOULDBLOCK
#define EWOULDBLOCK WSAEWOULDBLOCK
#endif
#ifndef ENOTSUP
#define ENOTSUP ENOSYS
#endif
#ifndef ETXTBSY
#define ETXTBSY EBUSY
#endif
#ifndef ENODATA
#define ENODATA ENOENT
#endif
#ifndef ELOOP
#define ELOOP 114
#endif

struct tm *win32_localtime_r(const time_t *timep, struct tm *result);
struct tm *win32_gmtime_r(const time_t *timep, struct tm *result);
#define localtime_r(t, r) win32_localtime_r((t), (r))
#define gmtime_r(t, r)    win32_gmtime_r((t), (r))

#define fchdir(fd)  (errno = ENOSYS, -1)
#define chroot(p)   (errno = ENOSYS, -1)

#define mknod(p, m, d)   (errno = ENOSYS, -1)
#define mkfifo(p, m)     (errno = ENOSYS, -1)
#define getgroups(n, g)  (0)
#define setgroups(n, g)  (0)
#define initgroups(u, g) (0)
#define fchmod(f, m)     (0)
#define fchown(f, u, g)  (0)

#ifndef major
#define major(d)        ((int)(((d) >> 8) & 0xff))
#define minor(d)        ((int)((d) & 0xff))
#define makedev(ma, mi) ((dev_t)(((ma) << 8) | (mi)))
#endif

#define LOG_PID     0x01
#define LOG_DAEMON  (3 << 3)
#define LOG_USER    (1 << 3)
#define LOG_ERR     3
#define LOG_WARNING 4
#define LOG_NOTICE  5
#define LOG_INFO    6
#define LOG_CRIT    2
#define openlog(ident, opt, fac)  ((void)0)
#define closelog()                ((void)0)
#define syslog(pri, ...)          ((void)0)

void win32_normalize_path(char *path);

#endif

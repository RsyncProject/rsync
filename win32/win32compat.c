/*
 * Windows implementations of the POSIX calls rsync makes: file metadata,
 * links, users/groups and assorted odds and ends.
 *
 * Copyright (C) 2026 Max Vilimpoc, rsync CMake/Windows port.
 * Distributed under the same GPL-3.0-or-later terms as the rest of rsync.
 */

#include "rsync.h"
#include "win32/win32undef.h"

#include <lmcons.h>
#include <locale.h>
#include <wincrypt.h>

void win32_init(void)
{
	WSADATA wsa;

	win32_harden();

	if (WSAStartup(MAKEWORD(2, 2), &wsa) != 0) {
		fprintf(stderr, "rsync: WSAStartup failed\n");
		exit(1);
	}

	_setmode(0, _O_BINARY);
	_setmode(1, _O_BINARY);

	setlocale(LC_ALL, ".UTF8");
	SetConsoleOutputCP(CP_UTF8);
	SetConsoleCP(CP_UTF8);

	win32_links_init();
}

int win32_no_fork(void)
{
	errno = ENOSYS;
	return -1;
}

void win32_normalize_path(char *path)
{
	char *p;

	for (p = path; *p; p++) {
		if (*p == '\\')
			*p = '/';
	}
}

int win32_oserr_to_errno(unsigned long err)
{
	switch (err) {
	case ERROR_FILE_NOT_FOUND:
	case ERROR_PATH_NOT_FOUND:
	case ERROR_INVALID_NAME:
	case ERROR_BAD_NETPATH:
	case ERROR_BAD_PATHNAME:        return ENOENT;
	case ERROR_ACCESS_DENIED:
	case ERROR_SHARING_VIOLATION:
	case ERROR_LOCK_VIOLATION:      return EACCES;
	case ERROR_ALREADY_EXISTS:
	case ERROR_FILE_EXISTS:         return EEXIST;
	case ERROR_NOT_SAME_DEVICE:     return EXDEV;
	case ERROR_PRIVILEGE_NOT_HELD:  return EPERM;
	case ERROR_DIR_NOT_EMPTY:       return ENOTEMPTY;
	case ERROR_TOO_MANY_OPEN_FILES: return EMFILE;
	case ERROR_NOT_ENOUGH_MEMORY:
	case ERROR_OUTOFMEMORY:         return ENOMEM;
	case ERROR_DISK_FULL:           return ENOSPC;
	case ERROR_WRITE_PROTECT:       return EROFS;
	case ERROR_INVALID_HANDLE:      return EBADF;
	default:                        return EIO;
	}
}

char *win32_strerror(int err)
{
	switch (err) {
	case ECONNRESET:   return "Connection reset by peer";
	case ECONNREFUSED: return "Connection refused";
	case ECONNABORTED: return "Software caused connection abort";
	case ENOTCONN:     return "Socket is not connected";
	case EADDRINUSE:   return "Address already in use";
	case ETIMEDOUT:    return "Connection timed out";
	case EHOSTUNREACH: return "No route to host";
	case ENETUNREACH:  return "Network is unreachable";
	case EAFNOSUPPORT: return "Address family not supported";
	case EINPROGRESS:  return "Operation now in progress";
	default:           return strerror(err);
	}
}

static int os_fail(void)
{
	errno = win32_oserr_to_errno(GetLastError());
	return -1;
}

int win32_open(const char *path, int flags, ...)
{
	va_list ap;
	int mode = 0;
	int fd;

	if (flags & _O_CREAT) {
		va_start(ap, flags);
		mode = va_arg(ap, int);
		va_end(ap);
	}

	flags &= ~(O_NOFOLLOW | O_DIRECTORY | O_CLOEXEC | O_NOATIME | O_NOCTTY | O_NONBLOCK);
	flags |= _O_BINARY;

	fd = _open(path, flags, _S_IREAD | ((mode & 0200) ? _S_IWRITE : 0));
	return fd;
}

int win32_attrs_are_symlink(unsigned long attrs, unsigned long tag)
{
	return (attrs & FILE_ATTRIBUTE_REPARSE_POINT)
	    && (tag == IO_REPARSE_TAG_SYMLINK || tag == IO_REPARSE_TAG_MOUNT_POINT);
}

static int path_is_symlink(const char *path)
{
	WIN32_FIND_DATAA fd;
	HANDLE h;
	DWORD attrs = GetFileAttributesA(path);

	if (attrs == INVALID_FILE_ATTRIBUTES
	 || !(attrs & FILE_ATTRIBUTE_REPARSE_POINT))
		return 0;

	h = FindFirstFileA(path, &fd);
	if (h == INVALID_HANDLE_VALUE)
		return 0;
	FindClose(h);

	return win32_attrs_are_symlink(attrs, fd.dwReserved0);
}

static const char *trim_path(const char *path, char *buf, size_t bufsz)
{
	size_t len = strlen(path);

	if (len < 2 || len >= bufsz)
		return path;
	if (path[len - 1] != '/' && path[len - 1] != '\\')
		return path;

	if (len == 3 && path[1] == ':')
		return path;

	memcpy(buf, path, len - 1);
	buf[len - 1] = '\0';
	return buf;
}

static __time64_t filetime_to_time(const FILETIME *ft)
{
	unsigned __int64 ticks =
		((unsigned __int64)ft->dwHighDateTime << 32) | ft->dwLowDateTime;

	if (ticks < 116444736000000000ULL)
		return 0;
	return (__time64_t)((ticks - 116444736000000000ULL) / 10000000ULL);
}

static int stat_by_handle(HANDLE h, const char *path,
			  struct win32_stat *st, int is_link)
{
	BY_HANDLE_FILE_INFORMATION bhfi;

	if (!GetFileInformationByHandle(h, &bhfi))
		return os_fail();

	memset(st, 0, sizeof *st);

	st->st_dev = (dev_t)bhfi.dwVolumeSerialNumber;
	st->st_rdev = st->st_dev;
	st->st_ino = ((unsigned __int64)bhfi.nFileIndexHigh << 32)
		   | bhfi.nFileIndexLow;
	st->st_nlink = (nlink_t)(bhfi.nNumberOfLinks ? bhfi.nNumberOfLinks : 1);
	if (bhfi.nNumberOfLinks > 1 && path)
		win32_note_link(path, WIN32_LINK_HARDLINK);
	st->st_size = ((__int64)bhfi.nFileSizeHigh << 32) | bhfi.nFileSizeLow;
	st->st_uid = WIN32_FAKE_UID;
	st->st_gid = WIN32_FAKE_GID;

	st->st_atime = filetime_to_time(&bhfi.ftLastAccessTime);
	st->st_mtime = filetime_to_time(&bhfi.ftLastWriteTime);
	st->st_ctime = filetime_to_time(&bhfi.ftCreationTime);

	if (is_link)
		st->st_mode = S_IFLNK | 0777;
	else if (bhfi.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY)
		st->st_mode = _S_IFDIR | 0777;
	else
		st->st_mode = _S_IFREG | 0666;

	if (!is_link && (bhfi.dwFileAttributes & FILE_ATTRIBUTE_READONLY))
		st->st_mode &= ~(mode_t)0222;

	return 0;
}

static int stat_path(const char *path, struct win32_stat *st, int follow)
{
	char buf[MAXPATHLEN];
	const char *p = trim_path(path, buf, sizeof buf);
	DWORD flags = FILE_FLAG_BACKUP_SEMANTICS;
	int is_link = 0;
	HANDLE h;
	int rc;

	if (path_is_symlink(p)) {
		win32_note_link(p, WIN32_LINK_SYMLINK);
		if (!follow) {
			is_link = 1;
			flags |= FILE_FLAG_OPEN_REPARSE_POINT;
		}
	}

	h = CreateFileA(p, FILE_READ_ATTRIBUTES,
			FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
			NULL, OPEN_EXISTING, flags, NULL);
	if (h == INVALID_HANDLE_VALUE)
		return os_fail();

	rc = stat_by_handle(h, p, st, is_link);
	CloseHandle(h);
	return rc;
}

int win32_stat(const char *path, struct win32_stat *st)
{
	return stat_path(path, st, 1);
}

int win32_lstat(const char *path, struct win32_stat *st)
{
	return stat_path(path, st, 0);
}

int win32_fstat(int fd, struct win32_stat *st)
{
	HANDLE h = (HANDLE)_get_osfhandle(fd);

	if (h == INVALID_HANDLE_VALUE) {
		errno = EBADF;
		return -1;
	}
	return stat_by_handle(h, NULL, st, 0);
}

int win32_chown(const char *path, uid_t uid, gid_t gid)
{
	(void)path; (void)uid; (void)gid;
	return 0;
}

int win32_chmod(const char *path, mode_t mode)
{
	DWORD attrs = GetFileAttributesA(path);

	if (attrs == INVALID_FILE_ATTRIBUTES)
		return os_fail();

	if (mode & S_IWUSR)
		attrs &= ~(DWORD)FILE_ATTRIBUTE_READONLY;
	else
		attrs |= FILE_ATTRIBUTE_READONLY;

	if (!SetFileAttributesA(path, attrs))
		return os_fail();
	return 0;
}

int win32_mkdir(const char *path, mode_t mode)
{
	(void)mode;
	if (_mkdir(path) != 0)
		return -1;
	return 0;
}

int win32_unlink(const char *path)
{
	DWORD attrs = GetFileAttributesA(path);

	if (attrs != INVALID_FILE_ATTRIBUTES && (attrs & FILE_ATTRIBUTE_READONLY))
		SetFileAttributesA(path, attrs & ~(DWORD)FILE_ATTRIBUTE_READONLY);

	if (attrs != INVALID_FILE_ATTRIBUTES
	 && (attrs & FILE_ATTRIBUTE_DIRECTORY)
	 && (attrs & FILE_ATTRIBUTE_REPARSE_POINT)) {
		if (RemoveDirectoryA(path))
			return 0;
		return os_fail();
	}

	if (!DeleteFileA(path))
		return os_fail();
	return 0;
}

int win32_rename(const char *from, const char *to)
{

	if (!MoveFileExA(from, to, MOVEFILE_REPLACE_EXISTING | MOVEFILE_COPY_ALLOWED))
		return os_fail();
	return 0;
}

int win32_ftruncate64(int fd, off64_t length)
{
	return _chsize_s(fd, length) == 0 ? 0 : -1;
}

int win32_fsync(int fd)
{
	HANDLE h = (HANDLE)_get_osfhandle(fd);

	if (h == INVALID_HANDLE_VALUE) {
		errno = EBADF;
		return -1;
	}
	if (!FlushFileBuffers(h)) {
		errno = EIO;
		return -1;
	}
	return 0;
}

int win32_mkstemp64(char *tmpl)
{
	size_t len = strlen(tmpl);
	HCRYPTPROV provider = 0;
	int tries;

	if (len < 6 || strcmp(tmpl + len - 6, "XXXXXX") != 0) {
		errno = EINVAL;
		return -1;
	}
	if (!CryptAcquireContextA(&provider, NULL, NULL, PROV_RSA_FULL,
				 CRYPT_VERIFYCONTEXT | CRYPT_SILENT)) {
		errno = EIO;
		return -1;
	}

	for (tries = 0; tries < 256; tries++) {
		static const char chars[] =
			"abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789";
		unsigned char random_bytes[6];
		int i, fd;

		if (!CryptGenRandom(provider, sizeof random_bytes, random_bytes)) {
			CryptReleaseContext(provider, 0);
			errno = EIO;
			return -1;
		}
		for (i = 0; i < 6; i++) {
			tmpl[len - 6 + i] = chars[random_bytes[i] % (sizeof chars - 1)];
		}

		fd = _open(tmpl, _O_RDWR | _O_CREAT | _O_EXCL | _O_BINARY,
			   _S_IREAD | _S_IWRITE);
		if (fd >= 0) {
			CryptReleaseContext(provider, 0);
			return fd;
		}
		if (errno != EEXIST) {
			CryptReleaseContext(provider, 0);
			return -1;
		}
	}
	CryptReleaseContext(provider, 0);
	errno = EEXIST;
	return -1;
}

static void timeval_to_filetime(const struct timeval *tv, FILETIME *ft)
{

	unsigned __int64 ticks =
		((unsigned __int64)tv->tv_sec + 11644473600ULL) * 10000000ULL
		+ (unsigned __int64)tv->tv_usec * 10ULL;

	ft->dwLowDateTime = (DWORD)(ticks & 0xFFFFFFFF);
	ft->dwHighDateTime = (DWORD)(ticks >> 32);
}

int win32_utimes(const char *path, const struct timeval tv[2])
{
	FILETIME atime, mtime;
	char buf[MAXPATHLEN];
	const char *p;
	size_t len;
	HANDLE h;
	BOOL ok;

	len = strlen(path);
	if (len >= 2 && path[len-1] == '.'
	 && (path[len-2] == '/' || path[len-2] == '\\')) {
		len -= (len == 2) ? 1 : 2;
		if (len >= sizeof buf)
			len = sizeof buf - 1;
		memcpy(buf, path, len);
		buf[len] = '\0';
		p = buf;
	} else
		p = path;

	h = CreateFileA(p, FILE_WRITE_ATTRIBUTES,
			FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
			NULL, OPEN_EXISTING, FILE_FLAG_BACKUP_SEMANTICS, NULL);
	if (h == INVALID_HANDLE_VALUE) {
		errno = (GetLastError() == ERROR_ACCESS_DENIED) ? EACCES : ENOENT;
		return -1;
	}

	timeval_to_filetime(&tv[0], &atime);
	timeval_to_filetime(&tv[1], &mtime);

	ok = SetFileTime(h, NULL, &atime, &mtime);
	CloseHandle(h);

	if (!ok) {
		errno = EACCES;
		return -1;
	}
	return 0;
}

win32_sighandler_t win32_signal(int sig, win32_sighandler_t handler)
{
	switch (sig) {
	case SIGABRT:
	case SIGFPE:
	case SIGILL:
	case SIGINT:
	case SIGSEGV:
	case SIGTERM:
		return (win32_sighandler_t)signal(sig, (void (*)(int))handler);
	default:
		return NULL;
	}
}

struct tm *win32_localtime_r(const time_t *timep, struct tm *result)
{
	if (localtime_s(result, timep) != 0)
		return NULL;
	return result;
}

struct tm *win32_gmtime_r(const time_t *timep, struct tm *result)
{
	if (gmtime_s(result, timep) != 0)
		return NULL;
	return result;
}

char *win32_getpass(const char *prompt)
{
	static char buf[256];
	HANDLE in = GetStdHandle(STD_INPUT_HANDLE);
	DWORD saved_mode = 0;
	size_t len;

	fputs(prompt, stderr);
	fflush(stderr);

	if (GetConsoleMode(in, &saved_mode))
		SetConsoleMode(in, saved_mode & ~(DWORD)ENABLE_ECHO_INPUT);

	if (!fgets(buf, sizeof buf, stdin))
		buf[0] = '\0';

	if (saved_mode)
		SetConsoleMode(in, saved_mode);

	fputs("\n", stderr);

	len = strlen(buf);
	while (len && (buf[len - 1] == '\n' || buf[len - 1] == '\r'))
		buf[--len] = '\0';

	return buf;
}

int win32_gettimeofday(struct timeval *tv, void *tz)
{
	FILETIME ft;
	unsigned __int64 ticks;

	(void)tz;
	GetSystemTimeAsFileTime(&ft);
	ticks = ((unsigned __int64)ft.dwHighDateTime << 32) | ft.dwLowDateTime;
	ticks -= 11644473600ULL * 10000000ULL;

	tv->tv_sec = (long)(ticks / 10000000ULL);
	tv->tv_usec = (long)((ticks % 10000000ULL) / 10);
	return 0;
}

unsigned int win32_sleep(unsigned int seconds)
{
	Sleep(seconds * 1000);
	return 0;
}

int win32_usleep(unsigned int usec)
{
	Sleep(usec / 1000);
	return 0;
}

int win32_gethostname(char *name, size_t len)
{
	DWORD sz = (DWORD)len;

	if (!GetComputerNameA(name, &sz)) {
		strncpy(name, "localhost", len - 1);
		name[len - 1] = '\0';
	}
	return 0;
}

static char  cur_user[UNLEN + 1];
static char  empty_str[] = "";
static char *no_members[] = { NULL };

static const char *current_user(void)
{
	DWORD sz = sizeof cur_user;

	if (!cur_user[0] && !GetUserNameA(cur_user, &sz))
		strcpy(cur_user, "user");
	return cur_user;
}

struct passwd *win32_getpwuid(uid_t uid)
{
	static struct passwd pw;

	if (uid != WIN32_FAKE_UID)
		return NULL;

	pw.pw_name = (char *)current_user();
	pw.pw_passwd = empty_str;
	pw.pw_uid = WIN32_FAKE_UID;
	pw.pw_gid = WIN32_FAKE_GID;
	pw.pw_gecos = empty_str;
	pw.pw_dir = getenv("USERPROFILE") ? getenv("USERPROFILE") : empty_str;
	pw.pw_shell = empty_str;
	return &pw;
}

struct passwd *win32_getpwnam(const char *name)
{
	if (name && _stricmp(name, current_user()) == 0)
		return win32_getpwuid(WIN32_FAKE_UID);
	return NULL;
}

struct group *win32_getgrgid(gid_t gid)
{
	static struct group gr;

	if (gid != WIN32_FAKE_GID)
		return NULL;

	gr.gr_name = (char *)current_user();
	gr.gr_passwd = empty_str;
	gr.gr_gid = WIN32_FAKE_GID;
	gr.gr_mem = no_members;
	return &gr;
}

struct group *win32_getgrnam(const char *name)
{
	if (name && _stricmp(name, current_user()) == 0)
		return win32_getgrgid(WIN32_FAKE_GID);
	return NULL;
}

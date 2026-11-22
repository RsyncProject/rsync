#define _GNU_SOURCE
#undef _FILE_OFFSET_BITS
#undef _TIME_BITS
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <stdarg.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#undef openat64

static int open_file(const char *symbol, int dirfd, const char *name, int flags, va_list args)
{
	static const char initial[] = "created by another writer\n";
	static int calls, parent;
	int (*original)(int, const char *, int, ...) = dlsym(RTLD_NEXT, symbol);
	const char *scenario = getenv("RSYNC_TEST_LOG_CREATE");
	mode_t mode = 0;
	int fd;

	if ((flags & O_CREAT) || (flags & O_TMPFILE) == O_TMPFILE)
		mode = va_arg(args, mode_t);
	if (!scenario || strcmp(name, "raced.log") != 0)
		return original(dirfd, name, flags, mode);

	if (!(flags & O_APPEND) || !(flags & O_NOFOLLOW) || (flags & (O_EXCL | O_TRUNC)))
		_exit(99);
	if (calls++) {
		if (calls != 2 || dirfd != parent || (flags & O_CREAT))
			_exit(99);
		return original(dirfd, name, flags, mode);
	}
	if (!(flags & O_CREAT))
		_exit(99);
	parent = dirfd;
	if (strcmp(scenario, "created") == 0) {
		fd = original(dirfd, name, flags | O_EXCL, mode);
		if (fd < 0 || write(fd, initial, sizeof initial - 1) != sizeof initial - 1 || close(fd) < 0)
			_exit(99);
	}
	errno = ENOENT;
	return -1;
}

int openat(int dirfd, const char *name, int flags, ...)
{
	va_list args;
	int fd;
	va_start(args, flags);
	fd = open_file("openat", dirfd, name, flags, args);
	va_end(args);
	return fd;
}

int openat64(int dirfd, const char *name, int flags, ...)
{
	va_list args;
	int fd;
	va_start(args, flags);
	fd = open_file("openat64", dirfd, name, flags, args);
	va_end(args);
	return fd;
}

#define _GNU_SOURCE
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <stdarg.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

static int parent_fd = -1, failed_fd = -1;

ssize_t readlinkat(int fd, const char *path, char *buffer, size_t length)
{
	ssize_t (*original)(int, const char *, char *, size_t) = dlsym(RTLD_NEXT, "readlinkat");
	ssize_t result = original(fd, path, buffer, length);
	if (result < 0 && errno == EINVAL && getenv("RSYNC_TEST_OPEN_FILE")
	 && strcmp(path, getenv("RSYNC_TEST_OPEN_FILE")) == 0)
		parent_fd = fd;
	return result;
}

static int open_file(const char *function, int fd, const char *path, int flags, mode_t mode)
{
	int (*original)(int, const char *, int, ...) = dlsym(RTLD_NEXT, function);
	int error = getenv("RSYNC_TEST_OPEN_ERRNO") ? atoi(getenv("RSYNC_TEST_OPEN_ERRNO")) : 0;
	if (error && fd == parent_fd && strcmp(path, getenv("RSYNC_TEST_OPEN_FILE")) == 0) {
		int marker = open(getenv("RSYNC_TEST_OPEN_MARKER"), O_WRONLY | O_CREAT, 0600);
		if (marker >= 0)
			close(marker);
		failed_fd = fd;
		errno = error;
		return -1;
	}
	return original(fd, path, flags, mode);
}

int openat(int fd, const char *path, int flags, ...)
{
	va_list args;
	mode_t mode = 0;
	va_start(args, flags);
	if ((flags & O_CREAT) || (flags & O_TMPFILE) == O_TMPFILE)
		mode = va_arg(args, mode_t);
	va_end(args);
	return open_file("openat", fd, path, flags, mode);
}

int openat64(int fd, const char *path, int flags, ...)
{
	va_list args;
	mode_t mode = 0;
	va_start(args, flags);
	if ((flags & O_CREAT) || (flags & O_TMPFILE) == O_TMPFILE)
		mode = va_arg(args, mode_t);
	va_end(args);
	return open_file("openat64", fd, path, flags, mode);
}

int close(int fd)
{
	int (*original)(int) = dlsym(RTLD_NEXT, "close");
	int result = original(fd);
	if (fd == failed_fd) {
		failed_fd = -1;
		errno = EBADF;
	}
	return result;
}

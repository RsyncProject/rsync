#define _GNU_SOURCE
#undef _FILE_OFFSET_BITS
#undef _TIME_BITS
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <stdarg.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#undef openat64

static int open_file(const char *symbol, int dirfd, const char *name, int flags, va_list args)
{
	int (*original)(int, const char *, int, ...) = dlsym(RTLD_NEXT, symbol);
	const char *parent = getenv("RSYNC_TEST_BASIS_PARENT");
	const char *marker = getenv("RSYNC_TEST_BASIS_MARKER");
	struct stat64 expected, actual;
	mode_t mode = 0;

	if ((flags & O_CREAT) || (flags & O_TMPFILE) == O_TMPFILE)
		mode = va_arg(args, mode_t);
	if (!original) {
		errno = ENOSYS;
		return -1;
	}
	if (parent && marker && strcmp(name, "episode") == 0
	 && (flags & (O_CREAT | O_ACCMODE)) == O_RDONLY
	 && stat64(parent, &expected) == 0 && fstat64(dirfd, &actual) == 0
	 && actual.st_dev == expected.st_dev && actual.st_ino == expected.st_ino) {
		/* The generator opens this basis before requesting the delta.
		 * The receiver sees the marker when it opens the same basis. */
		int fd = open(marker, O_WRONLY | O_CREAT | O_EXCL, 0600);
		if (fd < 0) {
			if (errno == EEXIST)
				errno = ENOENT;
			return -1;
		}
		close(fd);
	}
	return original(dirfd, name, flags, mode);
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

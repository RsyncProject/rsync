#define _GNU_SOURCE
#define _FILE_OFFSET_BITS 64
#include <errno.h>
#include <fcntl.h>
#include <stdarg.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

/* Include the time types before dlsym for 32-bit musl. */
#include <dlfcn.h>

static int needs_mode(int flags)
{
	return (flags & O_CREAT) || (flags & O_TMPFILE) == O_TMPFILE;
}

static int deny_reopen(int dirfd, const char *name, int flags)
{
	const char *parent = getenv("RSYNC_TEST_BASIS_PARENT");
	const char *marker = getenv("RSYNC_TEST_BASIS_MARKER");
	struct stat expected, actual;
	int fd;

	if (!parent || !marker || (flags & O_ACCMODE) != O_RDONLY || needs_mode(flags)
	 || strcmp(name, "episode") != 0 || stat(parent, &expected) < 0 || fstat(dirfd, &actual) < 0
	 || actual.st_dev != expected.st_dev || actual.st_ino != expected.st_ino)
		return 0;

	/* The generator opens this basis before requesting the delta.
	 * The receiver sees the marker when it opens the same basis. */
	fd = open(marker, O_WRONLY | O_CREAT | O_EXCL, 0600);
	if (fd < 0) {
		if (errno == EEXIST)
			errno = ENOENT;
		return 1;
	}
	close(fd);
	return 0;
}

static int open_file(const char *symbol, int dirfd, const char *name, int flags, va_list args)
{
	int (*original)(int, const char *, int, ...) = dlsym(RTLD_NEXT, symbol);
	mode_t mode = 0;

	if (!original) {
		errno = ENOSYS;
		return -1;
	}
	if (deny_reopen(dirfd, name, flags))
		return -1;
	if (needs_mode(flags))
		mode = va_arg(args, mode_t);
	return original(dirfd, name, flags, mode);
}

/* Name the exported symbols explicitly so large-file redirects do not
 * rename a wrapper through the libc declarations. */
#define OPENAT_HOOK(symbol) \
	int hook_##symbol(int dirfd, const char *name, int flags, ...) __asm__(#symbol); \
	int hook_##symbol(int dirfd, const char *name, int flags, ...) \
	{ \
		va_list args; \
		int fd; \
		va_start(args, flags); \
		fd = open_file(#symbol, dirfd, name, flags, args); \
		va_end(args); \
		return fd; \
	}

OPENAT_HOOK(openat)
OPENAT_HOOK(openat64)

#ifdef __GLIBC__
/* The fortified entry points have no variadic mode argument. */
#define CHECKED_OPENAT_HOOK(symbol) \
	int hook_##symbol(int dirfd, const char *name, int flags) __asm__(#symbol); \
	int hook_##symbol(int dirfd, const char *name, int flags) \
	{ \
		int (*original)(int, const char *, int) = dlsym(RTLD_NEXT, #symbol); \
		if (!original) { \
			errno = ENOSYS; \
			return -1; \
		} \
		if (deny_reopen(dirfd, name, flags)) \
			return -1; \
		return original(dirfd, name, flags); \
	}

CHECKED_OPENAT_HOOK(__openat_2)
CHECKED_OPENAT_HOOK(__openat64_2)
#endif

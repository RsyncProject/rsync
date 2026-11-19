#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

/* Declare the time types before dlsym so 32-bit musl selects the time64 ABI. */
#include <dlfcn.h>

static int target_fd(int fd, int directory_fd)
{
	const char *directory = getenv("RSYNC_TEST_CHMOD_DIR");
	char descriptor[64], path[PATH_MAX];
	ssize_t length;
	size_t directory_length;

	if (!directory)
		return 0;
	snprintf(descriptor, sizeof descriptor, "/proc/self/fd/%d", fd);
	if ((length = readlink(descriptor, path, sizeof path - 1)) < 0)
		return 0;
	path[length] = '\0';
	directory_length = strlen(directory);
	return strncmp(path, directory, directory_length) == 0
	    && path[directory_length] == (directory_fd ? '\0' : '/');
}

static void record_failure(const char *error)
{
	const char *marker_path = getenv("RSYNC_TEST_CHMOD_MARKER");

	if (marker_path) {
		int marker = open(marker_path, O_WRONLY | O_CREAT, 0600);
		if (marker >= 0)
			close(marker);
	}
	errno = atoi(error);
}

int fchmod(int fd, mode_t mode)
{
	int (*original)(int, mode_t) = dlsym(RTLD_NEXT, "fchmod");
	const char *error = getenv("RSYNC_TEST_CHMOD_ERRNO");

	if (error && target_fd(fd, 0)) {
		record_failure(error);
		return -1;
	}
	return original(fd, mode);
}

static int target_temporary_file(int fd, const char *name, int flags)
{
	static const char temporary_prefix[] = ".payload.";

	return name && (flags & AT_SYMLINK_NOFOLLOW)
	    && strncmp(name, temporary_prefix, sizeof temporary_prefix - 1) == 0
	    && target_fd(fd, 1);
}

static int lookup_armed;

static int deny_lookup(int fd, const char *name, int flags)
{
	static int injected;
	const char *error = getenv("RSYNC_TEST_LOOKUP_ERRNO");

	if (!lookup_armed || injected || !error || !target_temporary_file(fd, name, flags))
		return 0;
	injected = 1;
	record_failure(error);
	return 1;
}

/* Hook the libc spellings used by both the native and large-file builds. */
#define STATAT_HOOK(symbol, signature, arguments) \
	int symbol signature \
	{ \
		int (*original) signature = dlsym(RTLD_NEXT, #symbol); \
		if (deny_lookup(fd, name, flags)) \
			return -1; \
		return original arguments; \
	}

STATAT_HOOK(fstatat, (int fd, const char *name, struct stat *st, int flags),
	    (fd, name, st, flags))
#ifdef __GLIBC__
STATAT_HOOK(fstatat64, (int fd, const char *name, struct stat64 *st, int flags),
	    (fd, name, st, flags))
STATAT_HOOK(__fxstatat, (int ver, int fd, const char *name, struct stat *st, int flags),
	    (ver, fd, name, st, flags))
STATAT_HOOK(__fxstatat64, (int ver, int fd, const char *name, struct stat64 *st, int flags),
	    (ver, fd, name, st, flags))
STATAT_HOOK(__fstatat64_time64, (int fd, const char *name, void *st, int flags),
	    (fd, name, st, flags))
#endif

/* Setting times is between the initial stat and the final mode update. */
#define UTIMENSAT_HOOK(symbol, signature) \
	int symbol signature \
	{ \
		int (*original) signature = dlsym(RTLD_NEXT, #symbol); \
		int result = original(fd, name, times, flags); \
		if (result == 0 && getenv("RSYNC_TEST_LOOKUP_ERRNO") \
		 && target_temporary_file(fd, name, flags)) \
			lookup_armed = 1; \
		return result; \
	}

UTIMENSAT_HOOK(utimensat, (int fd, const char *name, const struct timespec times[2], int flags))
#ifdef __GLIBC__
UTIMENSAT_HOOK(__utimensat64, (int fd, const char *name, const void *times, int flags))
#endif

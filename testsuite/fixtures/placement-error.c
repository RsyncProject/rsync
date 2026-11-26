#define _GNU_SOURCE
#include <dlfcn.h>
#include <errno.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

static int refuse_rename(const char *name)
{
	const char *target = getenv("RSYNC_TEST_PLACEMENT_NAME");
	const char *slash = strrchr(name, '/');

	if (!target || strcmp(target, slash ? slash + 1 : name) != 0)
		return 0;
	errno = EIO;
	return 1;
}

int rename(const char *from, const char *to)
{
	int (*real_rename)(const char *, const char *) = dlsym(RTLD_NEXT, "rename");

	return refuse_rename(to) ? -1 : real_rename(from, to);
}

int renameat(int from_fd, const char *from, int to_fd, const char *to)
{
	int (*real_renameat)(int, const char *, int, const char *) = dlsym(RTLD_NEXT, "renameat");

	return refuse_rename(to) ? -1 : real_renameat(from_fd, from, to_fd, to);
}

ssize_t write(int fd, const void *data, size_t len)
{
	ssize_t (*real_write)(int, const void *, size_t) = dlsym(RTLD_NEXT, "write");
	const char *target = getenv("RSYNC_TEST_PLACEMENT_LOG");
	char desc[64], path[PATH_MAX];
	ssize_t n;

	if (target) {
		snprintf(desc, sizeof desc, "/proc/self/fd/%d", fd);
		n = readlink(desc, path, sizeof path - 1);
		if (n >= 0) {
			path[n] = '\0';
			if (strcmp(path, target) == 0) {
				errno = ENOSPC;
				return -1;
			}
		}
	}
	return real_write(fd, data, len);
}

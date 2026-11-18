#define _GNU_SOURCE
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

static int target_fd(int fd)
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
	    && path[directory_length] == '/';
}

int fchmod(int fd, mode_t mode)
{
	int (*original)(int, mode_t) = dlsym(RTLD_NEXT, "fchmod");

	if (target_fd(fd)) {
		const char *error_text = getenv("RSYNC_TEST_CHMOD_ERRNO");
		const char *marker_path = getenv("RSYNC_TEST_CHMOD_MARKER");
		if (marker_path) {
			int marker = open(marker_path, O_WRONLY | O_CREAT, 0600);
			if (marker >= 0)
				close(marker);
		}
		errno = error_text ? atoi(error_text) : EPERM;
		return -1;
	}
	return original(fd, mode);
}

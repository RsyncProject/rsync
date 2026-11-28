#define _GNU_SOURCE
#include <dlfcn.h>
#include <fcntl.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

ssize_t read(int fd, void *buffer, size_t size)
{
	static ssize_t (*real_read)(int, void *, size_t);
	static int second_reader = -1, changed;
	const char *path = getenv("RSYNC_TEST_BUDGET_BASIS");
	const char *marker = getenv("RSYNC_TEST_BUDGET_READER");
	char desc[64], name[PATH_MAX];
	ssize_t n;

	if (!real_read)
		real_read = dlsym(RTLD_NEXT, "read");
	n = real_read(fd, buffer, size);
	if (n > 0 && path && marker && !changed) {
		ssize_t len;

		snprintf(desc, sizeof desc, "/proc/self/fd/%d", fd);
		len = readlink(desc, name, sizeof name - 1);
		if (len < 0)
			return n;
		name[len] = '\0';
		if (strcmp(path, name) != 0)
			return n;
		/* The generator reads the basis first. Only corrupt the receiver's
		 * first read so the ordinary checksum retry can then succeed. */
		if (second_reader < 0) {
			int fd_marker = open(marker, O_WRONLY | O_CREAT | O_EXCL, 0600);
			second_reader = fd_marker < 0;
			if (fd_marker >= 0)
				close(fd_marker);
		}
		if (second_reader) {
			((unsigned char *)buffer)[0] ^= 1;
			changed = 1;
		}
	}
	return n;
}

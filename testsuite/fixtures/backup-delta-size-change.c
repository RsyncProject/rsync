#define _GNU_SOURCE 1
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

ssize_t read(int fd, void *buf, size_t count)
{
	static ssize_t (*real_read)(int, void *, size_t);
	static int changed;
	const char *path = getenv("RSYNC_TEST_OLD_FILE");
	const char *marker = getenv("RSYNC_TEST_CHANGE_MARKER");
	const char *mutation = getenv("RSYNC_TEST_BACKUP_MUTATION");
	struct stat open_file, original;
	ssize_t ret;
	int change_fd, mark, ok = 0;
	char extra[8192];

	if (!real_read)
		real_read = dlsym(RTLD_NEXT, "read");
	if (!real_read) {
		errno = EIO;
		return -1;
	}
	ret = real_read(fd, buf, count);
	if (changed || ret <= 0 || !path || !mutation || fstat(fd, &open_file) < 0
	 || stat(path, &original) < 0 || open_file.st_dev != original.st_dev
	 || open_file.st_ino != original.st_ino)
		return ret;
	changed = 1;
	if (strcmp(mutation, "shrink") == 0) {
		change_fd = open(path, O_WRONLY);
		if (change_fd >= 0) {
			ok = ftruncate(change_fd, 8192) == 0;
			close(change_fd);
		}
	} else if (strcmp(mutation, "grow") == 0) {
		memset(extra, 'z', sizeof extra);
		change_fd = open(path, O_WRONLY | O_APPEND);
		if (change_fd >= 0) {
			ok = write(change_fd, extra, sizeof extra) == sizeof extra;
			close(change_fd);
		}
	}
	mark = ok && marker ? open(marker, O_WRONLY | O_CREAT | O_EXCL, 0600) : -1;
	if (mark >= 0)
		close(mark);
	return ret;
}

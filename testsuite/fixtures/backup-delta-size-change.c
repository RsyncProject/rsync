#define _GNU_SOURCE 1
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

ssize_t write(int fd, const void *buf, size_t count)
{
	static ssize_t (*real_write)(int, const void *, size_t);
	static int changed;
	const char *basis = getenv("RSYNC_TEST_OLD_FILE");
	const char *backup = getenv("RSYNC_TEST_BACKUP_FILE");
	const char *marker = getenv("RSYNC_TEST_CHANGE_MARKER");
	const char *mutation = getenv("RSYNC_TEST_BACKUP_MUTATION");
	struct stat open_file, saved_file, original;
	ssize_t ret;
	int change_fd, mark, saved_errno, ok = 0;
	char extra[8192];

	if (!real_write)
		real_write = dlsym(RTLD_NEXT, "write");
	if (!real_write) {
		errno = EIO;
		return -1;
	}
	ret = real_write(fd, buf, count);
	if (changed || ret <= 0 || !basis || !backup || !marker || !mutation)
		return ret;

	/* The backup write identifies the copy phase regardless of how libc reads. */
	saved_errno = errno;
	if (fstat(fd, &open_file) < 0 || stat(backup, &saved_file) < 0
	 || open_file.st_dev != saved_file.st_dev || open_file.st_ino != saved_file.st_ino
	 || stat(basis, &original) < 0 || open_file.st_size >= original.st_size)
		goto done;

	changed = 1;
	if (strcmp(mutation, "shrink") == 0) {
		change_fd = open(basis, O_WRONLY);
		if (change_fd >= 0) {
			ok = ftruncate(change_fd, 8192) == 0;
			close(change_fd);
		}
	} else if (strcmp(mutation, "grow") == 0) {
		memset(extra, 'z', sizeof extra);
		change_fd = open(basis, O_WRONLY | O_APPEND);
		if (change_fd >= 0) {
			ok = real_write(change_fd, extra, sizeof extra) == sizeof extra;
			close(change_fd);
		}
	}
	mark = ok ? open(marker, O_WRONLY | O_CREAT | O_EXCL, 0600) : -1;
	if (mark >= 0)
		close(mark);
  done:
	errno = saved_errno;
	return ret;
}

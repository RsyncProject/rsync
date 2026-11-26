#define _GNU_SOURCE 1
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/xattr.h>
#include <unistd.h>

static int backup_fd(int fd)
{
	const char *path = getenv("RSYNC_TEST_BACKUP_FILE");
	struct stat open_file, backup;

	return path && fstat(fd, &open_file) == 0 && stat(path, &backup) == 0
		&& open_file.st_dev == backup.st_dev && open_file.st_ino == backup.st_ino;
}

static void mark_failure(void)
{
	const char *marker = getenv("RSYNC_TEST_BACKUP_MARKER");
	int mark = marker ? open(marker, O_WRONLY | O_CREAT | O_EXCL, 0600) : -1;
	if (mark >= 0)
		close(mark);
}

ssize_t write(int fd, const void *buf, size_t count)
{
	static ssize_t (*real_write)(int, const void *, size_t);
	static int injected;
	const char *failure = getenv("RSYNC_TEST_BACKUP_FAILURE");

	if (!real_write)
		real_write = dlsym(RTLD_NEXT, "write");
	if (!real_write) {
		errno = EIO;
		return -1;
	}
	if (!injected && failure && strcmp(failure, "write") == 0 && backup_fd(fd)) {
		injected = 1;
		mark_failure();
		errno = ENOSPC;
		return -1;
	}
	return real_write(fd, buf, count);
}

int fsetxattr(int fd, const char *name, const void *value, size_t size, int flags)
{
	static int (*real_fsetxattr)(int, const char *, const void *, size_t, int);
	static int injected;
	const char *failure = getenv("RSYNC_TEST_BACKUP_FAILURE");

	if (!real_fsetxattr)
		real_fsetxattr = dlsym(RTLD_NEXT, "fsetxattr");
	if (!real_fsetxattr) {
		errno = EIO;
		return -1;
	}
	if (!injected && failure && strcmp(failure, "xattr") == 0 && backup_fd(fd)) {
		injected = 1;
		mark_failure();
		errno = ENOSPC;
		return -1;
	}
	return real_fsetxattr(fd, name, value, size, flags);
}

int close(int fd)
{
	static int (*real_close)(int);
	static int injected;
	const char *failure = getenv("RSYNC_TEST_BACKUP_FAILURE");
	int ret;

	if (!real_close)
		real_close = dlsym(RTLD_NEXT, "close");
	if (!real_close) {
		errno = EIO;
		return -1;
	}
	if (!injected && failure && strcmp(failure, "close") == 0 && backup_fd(fd)) {
		injected = 1;
		mark_failure();
		ret = real_close(fd);
		if (ret == 0)
			errno = ENOSPC;
		return ret == 0 ? -1 : ret;
	}
	return real_close(fd);
}

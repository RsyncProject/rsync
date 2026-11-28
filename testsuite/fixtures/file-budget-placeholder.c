#define _GNU_SOURCE
#include <dlfcn.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

static void change_placeholder(int result, int dfd, const char *name)
{
	static int changed;
	const char *path = getenv("RSYNC_TEST_BUDGET_PLACEHOLDER");
	const char *marker = getenv("RSYNC_TEST_BUDGET_CHANGED");
	const char *replace = getenv("RSYNC_TEST_BUDGET_REPLACE");
	const char *trigger = getenv("RSYNC_TEST_BUDGET_TRIGGER");
	const char *directory = getenv("RSYNC_TEST_BUDGET_DIRECTORY");
	struct stat made, target;
	int fd;

	if (changed || result < 0 || !path || !marker || !replace
	 || fstatat(dfd, name, &made, AT_SYMLINK_NOFOLLOW) < 0
	 || lstat(trigger ? trigger : path, &target) < 0
	 || made.st_dev != target.st_dev || made.st_ino != target.st_ino || !S_ISDIR(target.st_mode))
		return;
	changed = 1;
	if (rmdir(path) < 0)
		return;
	if (strcmp(replace, "file") == 0) {
		const struct timespec times[2] = {{1500000000, 0}, {1500000000, 0}};
		fd = open(path, O_WRONLY | O_CREAT | O_EXCL, 0600);
		if (fd < 0)
			return;
		if (write(fd, "keep", 4) != 4 || futimens(fd, times) < 0) {
			close(fd);
			return;
		}
		close(fd);
	} else if (strcmp(replace, "directory") == 0) {
		if (!directory || rename(directory, path) < 0)
			return;
	}
	fd = open(marker, O_WRONLY | O_CREAT | O_EXCL, 0600);
	if (fd >= 0)
		close(fd);
}

int mkdirat(int dfd, const char *name, mode_t mode)
{
	static int (*real_mkdirat)(int, const char *, mode_t);
	int result;

	if (!real_mkdirat)
		real_mkdirat = dlsym(RTLD_NEXT, "mkdirat");
	result = real_mkdirat(dfd, name, mode);
	change_placeholder(result, dfd, name);
	return result;
}

int mkdir(const char *name, mode_t mode)
{
	static int (*real_mkdir)(const char *, mode_t);
	int result;

	if (!real_mkdir)
		real_mkdir = dlsym(RTLD_NEXT, "mkdir");
	result = real_mkdir(name, mode);
	change_placeholder(result, AT_FDCWD, name);
	return result;
}

#define _GNU_SOURCE
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

static int jailed, marker = -1;

int chroot(const char *path)
{
	int (*original)(const char *) = dlsym(RTLD_NEXT, "chroot");
	const char *target = getenv("RSYNC_TEST_CHROOT_PATH");
	int selected = target && strcmp(path, target) == 0;
	int result;
	if (selected)
		marker = open(getenv("RSYNC_TEST_CHROOT_MARKER"), O_WRONLY | O_CREAT | O_APPEND, 0600);
	result = original(path);
	if (selected && result == 0)
		jailed = 1;
	return result;
}

int chdir(const char *path)
{
	int (*original)(const char *) = dlsym(RTLD_NEXT, "chdir");
	if (jailed) {
		jailed = 0;
		if (marker >= 0) {
			(void)write(marker, path, strlen(path));
			(void)write(marker, "\n", 1);
		}
		if (getenv("RSYNC_TEST_CHROOT_DENY") && strcmp(path, "/") == 0) {
			errno = EACCES;
			return -1;
		}
	}
	return original(path);
}

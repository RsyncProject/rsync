#define _GNU_SOURCE 1
#include <dlfcn.h>
#include <errno.h>
#include <signal.h>
#include <stdlib.h>
#include <sys/stat.h>
#include <unistd.h>

int fchmod(int fd, mode_t mode)
{
	static int (*real_fchmod)(int, mode_t);
	const char *path = getenv("RSYNC_TEST_BACKUP_DIR");
	struct stat before, target;
	int ret;

	if (!real_fchmod)
		real_fchmod = dlsym(RTLD_NEXT, "fchmod");
	if (!real_fchmod) {
		errno = ENOSYS;
		return -1;
	}
	ret = real_fchmod(fd, mode);
	if (ret == 0 && path && (mode & S_IWUSR)
	 && fstat(fd, &before) == 0 && stat(path, &target) == 0
	 && before.st_dev == target.st_dev && before.st_ino == target.st_ino) {
		static const char marker[] = "test: backup directory made writable\n";
		(void)write(STDERR_FILENO, marker, sizeof marker - 1);
		raise(SIGUSR1);
	}
	return ret;
}

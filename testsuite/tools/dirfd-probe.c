#include "config.h"

#include <sys/stat.h>
#include <sys/types.h>
#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>

static const struct {
	const char *name;
	int value, defined;
} candidates[] = {
	{ "O_RDONLY", O_RDONLY, 1 },
#ifdef O_PATH
	{ "O_PATH", O_PATH, 1 },
#else
	{ "O_PATH", 0, 0 },
#endif
#ifdef O_SEARCH
	{ "O_SEARCH", O_SEARCH, 1 },
#else
	{ "O_SEARCH", 0, 0 },
#endif
#ifdef O_EXEC
	{ "O_EXEC", O_EXEC, 1 },
#else
	{ "O_EXEC", 0, 0 },
#endif
#ifdef O_EVTONLY
	{ "O_EVTONLY", O_EVTONLY, 1 },
#else
	{ "O_EVTONLY", 0, 0 },
#endif
};

static int file_op(int dirfd, const char *name, int create)
{
	int flags = create ? O_WRONLY | O_CREAT | O_EXCL : O_RDONLY;
	int fd = openat(dirfd, name, flags, 0600), error = 0;
	char byte = 'x';
	ssize_t count;

	if (fd < 0)
		return errno;
	count = create ? write(fd, &byte, 1) : read(fd, &byte, 1);
	if (count != 1)
		error = count < 0 ? errno : EIO;
	else if (!create && byte != 'x')
		error = EIO;
	if (close(fd) < 0 && !error)
		error = errno;
	return error;
}

static int chdir_op(int fd, int saved)
{
	int error = fchdir(fd) < 0 ? errno : 0;

	if (fchdir(saved) < 0)
		return -1;
	return error;
}

static int denied(int error)
{
	return error == EACCES || error == EPERM;
}

int main(int argc, char *argv[])
{
	static const mode_t modes[] = { 0700, 0100, 0300, 0000 };
	struct stat expected, observed;
	char path[PATH_MAX] = "", *parent = NULL;
	int workfd = -1, dirfd = -1, saved = -1, fd = -1;
	int common = O_DIRECTORY | O_NOFOLLOW, made = 0, status = 1, error;
	size_t i, j;

	if (argc != 2) {
		fprintf(stderr, "Usage: dirfd-probe SCRATCH_DIR\n");
		return 2;
	}
	if (getuid() == 0 || geteuid() == 0) {
		fprintf(stderr, "dirfd-probe requires a non-root process\n");
		return 2;
	}
	alarm(10);
	umask(0077);
#ifdef O_CLOEXEC
	common |= O_CLOEXEC;
#endif
	parent = realpath(argv[1], NULL);
	if (!parent)
		goto error;
	if (snprintf(path, sizeof path, "%s/dirfd.XXXXXX", parent) >= (int)sizeof path) {
		errno = ENAMETOOLONG;
		goto error;
	}
	if (!mkdtemp(path))
		goto error;
	made = 1;
	workfd = open(path, O_RDONLY | common);
	if (workfd < 0 || mkdirat(workfd, "dir", 0700) < 0)
		goto error;
	dirfd = openat(workfd, "dir", O_RDONLY | common);
	saved = open(".", O_RDONLY | common);
	if (dirfd < 0 || saved < 0 || fstat(dirfd, &expected) < 0
	 || symlinkat("dir", workfd, "link") < 0)
		goto error;
	if ((error = file_op(dirfd, "known", 1)) != 0) {
		errno = error;
		goto error;
	}
	if (fchmod(dirfd, 0100) < 0)
		goto error;
	if ((error = file_op(workfd, "dir/known", 0)) != 0) {
		errno = error;
		goto error;
	}
	fd = openat(workfd, "dir", O_RDONLY | common);
	if (fd >= 0 || !denied(errno) || !denied(file_op(workfd, "dir/new", 1))) {
		errno = EACCES;
		goto error;
	}
	if (fchmod(dirfd, 0000) < 0)
		goto error;
	if (!denied(file_op(workfd, "dir/known", 0))) {
		errno = EACCES;
		goto error;
	}
	if (fchmod(dirfd, 0700) < 0)
		goto error;

	printf("# euid=%ju egid=%ju device=%ju common=%#x\n",
	       (uintmax_t)geteuid(), (uintmax_t)getegid(), (uintmax_t)expected.st_dev, (unsigned)common);
	printf("flag\tvalue\tmode\topen\tfstat\tknown\tcreate\tfchdir\trevoked_known\trevoked_fchdir\tnofollow\n");
	for (i = 0; i < sizeof candidates / sizeof candidates[0]; i++) {
		int flags = candidates[i].value | common, nofollow;
		if (!candidates[i].defined) {
			printf("%s\tN/A\tN/A\tN/A\tN/A\tN/A\tN/A\tN/A\tN/A\tN/A\tN/A\n", candidates[i].name);
			continue;
		}
		fd = openat(workfd, "link", flags);
		nofollow = fd < 0 ? errno : 0;
		if (fd >= 0) {
			if (close(fd) < 0) {
				fd = -1;
				goto error;
			}
			fd = -1;
		}
		for (j = 0; j < sizeof modes / sizeof modes[0]; j++) {
			int stat_error, known, create, chdir_error, revoked_known, revoked_chdir;
			if (fchmod(dirfd, modes[j]) < 0)
				goto error;
			fd = openat(workfd, "dir", flags);
			if (fd < 0) {
				printf("%s\t%#x\t%04o\t%d\tN/A\tN/A\tN/A\tN/A\tN/A\tN/A\t%d\n",
				       candidates[i].name, (unsigned)candidates[i].value, (unsigned)modes[j], errno, nofollow);
				continue;
			}
			stat_error = fstat(fd, &observed) < 0 ? errno : 0;
			if (stat_error) {
				printf("%s\t%#x\t%04o\t0\t%d\tN/A\tN/A\tN/A\tN/A\tN/A\t%d\n",
				       candidates[i].name, (unsigned)candidates[i].value, (unsigned)modes[j], stat_error, nofollow);
			} else {
				if (observed.st_dev != expected.st_dev || observed.st_ino != expected.st_ino || !S_ISDIR(observed.st_mode)) {
					errno = ESTALE;
					goto error;
				}
				known = file_op(fd, "known", 0);
				create = file_op(fd, "new", 1);
				chdir_error = chdir_op(fd, saved);
				if (chdir_error < 0 || fchmod(dirfd, 0000) < 0)
					goto error;
				revoked_known = file_op(fd, "known", 0);
				revoked_chdir = chdir_op(fd, saved);
				if (revoked_chdir < 0 || fchmod(dirfd, 0700) < 0)
					goto error;
				if (unlinkat(dirfd, "new", 0) < 0 && errno != ENOENT)
					goto error;
				printf("%s\t%#x\t%04o\t0\t0\t%d\t%d\t%d\t%d\t%d\t%d\n",
				       candidates[i].name, (unsigned)candidates[i].value, (unsigned)modes[j],
				       known, create, chdir_error, revoked_known, revoked_chdir, nofollow);
			}
			if (close(fd) < 0) {
				fd = -1;
				goto error;
			}
			fd = -1;
		}
		if (fchmod(dirfd, 0700) < 0)
			goto error;
	}
	status = 0;
	goto out;
error:
	perror("dirfd-probe");
out:
	if (saved >= 0 && fchdir(saved) < 0)
		status = 1;
	if (fd >= 0)
		close(fd);
	if (dirfd >= 0) {
		if (fchmod(dirfd, 0700) < 0)
			status = 1;
		if (unlinkat(dirfd, "new", 0) < 0 && errno != ENOENT)
			status = 1;
		if (unlinkat(dirfd, "known", 0) < 0 && errno != ENOENT)
			status = 1;
		close(dirfd);
	}
	if (workfd >= 0) {
		if (unlinkat(workfd, "link", 0) < 0 && errno != ENOENT)
			status = 1;
		if (unlinkat(workfd, "dir", AT_REMOVEDIR) < 0 && errno != ENOENT)
			status = 1;
		close(workfd);
	}
	if (saved >= 0)
		close(saved);
	if (made && rmdir(path) < 0)
		status = 1;
	free(parent);
	if (fflush(stdout) < 0 || ferror(stdout))
		status = 1;
	return status;
}

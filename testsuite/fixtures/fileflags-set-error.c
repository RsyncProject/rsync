#include <sys/stat.h>
#include <errno.h>
#include <unistd.h>

int chflagsat(int fd, const char *path, unsigned long flags, int atflag)
{
	(void)fd;
	(void)path;
	(void)flags;
	(void)atflag;
	errno = EOPNOTSUPP;
	return -1;
}

#define _GNU_SOURCE 1
#include <errno.h>
#include <stdio.h>

FILE *tmpfile(void)
{
	errno = ENOENT;
	return NULL;
}

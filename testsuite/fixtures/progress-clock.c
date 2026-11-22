#include <sys/time.h>

int gettimeofday(struct timeval *tv, void *tz)
{
	static time_t seconds = 1700000000;

	(void)tz;
	tv->tv_sec = seconds++;
	tv->tv_usec = 0;
	return 0;
}

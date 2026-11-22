#include <sys/time.h>

/* glibc before 2.31 declares a timezone pointer; newer libc uses void. */
#if defined(__GLIBC__)
# if __GLIBC_PREREQ(2, 31)
#  define TEST_TIMEZONE_ARG void *
# else
#  define TEST_TIMEZONE_ARG struct timezone *
# endif
#else
# define TEST_TIMEZONE_ARG void *
#endif

int gettimeofday(struct timeval *tv, TEST_TIMEZONE_ARG tz)
{
	static time_t seconds = 1700000000;

	(void)tz;
	tv->tv_sec = seconds++;
	tv->tv_usec = 0;
	return 0;
}

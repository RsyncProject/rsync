/*
 * Reimplementations of standard functions for platforms that don't have them.
 *
 * Copyright (C) 1998 Andrew Tridgell
 * Copyright (C) 2002 Martin Pool
 * Copyright (C) 2004-2015 Wayne Davison
 *
 * This program is free software; you can redistribute it and/or modify
 * it under the terms of the GNU General Public License as published by
 * the Free Software Foundation; either version 3 of the License, or
 * (at your option) any later version.
 *
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License along
 * with this program; if not, visit the http://fsf.org website.
 */

#include "rsync.h"
#include "itypes.h"

static char number_separator;

#ifndef HAVE_STRDUP
 char *strdup(char *s)
{
	int len = strlen(s) + 1;
	char *ret = (char *)malloc(len);
	if (ret)
		memcpy(ret, s, len);
	return ret;
}
#endif

#ifndef HAVE_GETCWD
 char *getcwd(char *buf, int size)
{
	return getwd(buf);
}
#endif


#ifndef HAVE_WAITPID
 pid_t waitpid(pid_t pid, int *statptr, int options)
{
#ifdef HAVE_WAIT4
	return wait4(pid, statptr, options, NULL);
#else
	/* If wait4 is also not available, try wait3 for SVR3 variants */
	/* Less ideal because can't actually request a specific pid */
	/* At least the WNOHANG option is supported */
	/* Code borrowed from apache fragment written by dwd@bell-labs.com */
	int tmp_pid, dummystat;;
	if (kill(pid, 0) == -1) {
		errno = ECHILD;
		return -1;
	}
	if (statptr == NULL)
		statptr = &dummystat;
	while (((tmp_pid = wait3(statptr, options, 0)) != pid) &&
		    (tmp_pid != -1) && (tmp_pid != 0) && (pid != -1))
	    ;
	return tmp_pid;
#endif
}
#endif


#ifndef HAVE_MEMMOVE
 void *memmove(void *dest, const void *src, size_t n)
{
	bcopy((char *) src, (char *) dest, n);
	return dest;
}
#endif

#ifndef HAVE_STRPBRK
/**
 * Find the first ocurrence in @p s of any character in @p accept.
 *
 * Derived from glibc
 **/
 char *strpbrk(const char *s, const char *accept)
{
	while (*s != '\0')  {
		const char *a = accept;
		while (*a != '\0') {
			if (*a++ == *s)	return (char *)s;
		}
		++s;
	}

	return NULL;
}
#endif


#ifndef HAVE_STRLCPY
/**
 * Like strncpy but does not 0 fill the buffer and always null
 * terminates.
 *
 * @param bufsize is the size of the destination buffer.
 *
 * @return index of the terminating byte.
 **/
 size_t strlcpy(char *d, const char *s, size_t bufsize)
{
	size_t len = strlen(s);
	size_t ret = len;
	if (bufsize > 0) {
		if (len >= bufsize)
			len = bufsize-1;
		memcpy(d, s, len);
		d[len] = 0;
	}
	return ret;
}
#endif

#ifndef HAVE_STRLCAT
/**
 * Like strncat() but does not 0 fill the buffer and always null
 * terminates.
 *
 * @param bufsize length of the buffer, which should be one more than
 * the maximum resulting string length.
 **/
 size_t strlcat(char *d, const char *s, size_t bufsize)
{
	size_t len1 = strlen(d);
	size_t len2 = strlen(s);
	size_t ret = len1 + len2;

	if (len1 < bufsize - 1) {
		if (len2 >= bufsize - len1)
			len2 = bufsize - len1 - 1;
		memcpy(d+len1, s, len2);
		d[len1+len2] = 0;
	}
	return ret;
}
#endif

/* some systems don't take the 2nd argument */
int sys_gettimeofday(struct timeval *tv)
{
#ifdef HAVE_GETTIMEOFDAY_TZ
	return gettimeofday(tv, NULL);
#else
	return gettimeofday(tv);
#endif
}

#define HUMANIFY(mult) \
	do { \
		if (num >= mult || num <= -mult) { \
			double dnum = (double)num / mult; \
			char units; \
			if (num < 0) \
				dnum = -dnum; \
			if (dnum < mult) \
				units = 'K'; \
			else if ((dnum /= mult) < mult) \
				units = 'M'; \
			else if ((dnum /= mult) < mult) \
				units = 'G'; \
			else { \
				dnum /= mult; \
				units = 'T'; \
			} \
			if (num < 0) \
				dnum = -dnum; \
			snprintf(bufs[n], sizeof bufs[0], "%.2f%c", dnum, units); \
			return bufs[n]; \
		} \
	} while (0)

/* Return the int64 number as a string.  If the human_flag arg is non-zero,
 * we may output the number in K, M, G, or T units.  If we don't add a unit
 * suffix, we will append the fract string, if it is non-NULL.  We can
 * return up to 4 buffers at a time. */
char *do_big_num(int64 num, int human_flag, const char *fract)
{
	static char bufs[4][128]; /* more than enough room */
	static unsigned int n;
	char *s;
	int len, negated;

	if (human_flag && !number_separator) {
		char buf[32];
		snprintf(buf, sizeof buf, "%f", 3.14);
		if (strchr(buf, '.') != NULL)
			number_separator = ',';
		else
			number_separator = '.';
	}

	n = (n + 1) % (sizeof bufs / sizeof bufs[0]);

	if (human_flag > 1) {
		if (human_flag == 2)
			HUMANIFY(1000);
		else
			HUMANIFY(1024);
	}

	s = bufs[n] + sizeof bufs[0] - 1;
	if (fract) {
		len = strlen(fract);
		s -= len;
		strlcpy(s, fract, len + 1);
	} else
		*s = '\0';

	len = 0;

	if (!num)
		*--s = '0';
	if (num < 0) {
		/* A maximum-size negated number can't fit as a positive,
		 * so do one digit in negated form to start us off. */
		*--s = (char)(-(num % 10)) + '0';
		num = -(num / 10);
		len++;
		negated = 1;
	} else
		negated = 0;

	while (num) {
		if (human_flag) {
			if (len == 3) {
				*--s = number_separator;
				len = 1;
			} else
				len++;
		}
		*--s = (char)(num % 10) + '0';
		num /= 10;
	}

	if (negated)
		*--s = '-';

	return s;
}

/* Return the double number as a string.  If the human_flag option is > 1,
 * we may output the number in K, M, G, or T units.  The buffer we use for
 * our result is either a single static buffer defined here, or a buffer
 * we get from do_big_num(). */
char *do_big_dnum(double dnum, int human_flag, int decimal_digits)
{
	static char tmp_buf[128];
#if SIZEOF_INT64 >= 8
	char *fract;

	snprintf(tmp_buf, sizeof tmp_buf, "%.*f", decimal_digits, dnum);

	if (!human_flag || (dnum < 1000.0 && dnum > -1000.0))
		return tmp_buf;

	for (fract = tmp_buf+1; isDigit(fract); fract++) {}

	return do_big_num((int64)dnum, human_flag, fract);
#else
	/* A big number might lose digits converting to a too-short int64,
	 * so let's just return the raw double conversion. */
	snprintf(tmp_buf, sizeof tmp_buf, "%.*f", decimal_digits, dnum);
	return tmp_buf;
#endif
}

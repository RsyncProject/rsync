/*
 * Reimplementations of standard functions for platforms that don't have them.
 *
 * Copyright (C) 1998 Andrew Tridgell
 * Copyright (C) 2002 Martin Pool
 * Copyright (C) 2004, 2005, 2006 Wayne Davison
 *
 * This program is free software; you can redistribute it and/or modify
 * it under the terms of the GNU General Public License as published by
 * the Free Software Foundation; either version 2 of the License, or
 * (at your option) any later version.
 *
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License along
 * with this program; if not, write to the Free Software Foundation, Inc.,
 * 51 Franklin Street - Fifth Floor, Boston, MA 02110-1301, USA.
 */

#include "rsync.h"

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

/* 
   Copyright (C) Andrew Tridgell 1998
   Copyright (C) 2002 by Martin Pool
   
   This program is free software; you can redistribute it and/or modify
   it under the terms of the GNU General Public License as published by
   the Free Software Foundation; either version 2 of the License, or
   (at your option) any later version.
   
   This program is distributed in the hope that it will be useful,
   but WITHOUT ANY WARRANTY; without even the implied warranty of
   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
   GNU General Public License for more details.
   
   You should have received a copy of the GNU General Public License
   along with this program; if not, write to the Free Software
   Foundation, Inc., 675 Mass Ave, Cambridge, MA 02139, USA.
*/

/**
 * @file compat.c
 *
 * Reimplementations of standard functions for platforms that don't
 * have them.
 **/



#include "rsync.h"


#ifndef HAVE_STRDUP
 char *strdup(char *s)
{
  int l = strlen(s) + 1;
  char *ret = (char *)malloc(l);
  if (ret)
    strcpy(ret,s);
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
	if (bufsize <= 0) return 0;
	if (len >= bufsize) len = bufsize-1;
	memcpy(d, s, len);
	d[len] = 0;
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

	if (len1+len2 >= bufsize) {
		len2 = bufsize - (len1+1);
	}
	if (len2 > 0) {
		memcpy(d+len1, s, len2);
		d[len1+len2] = 0;
	}
	return ret;
}
#endif

#ifdef REPLACE_INET_NTOA
 char *rep_inet_ntoa(struct in_addr ip)
{
	unsigned char *p = (unsigned char *)&ip.s_addr;
	static char buf[18];
#if WORDS_BIGENDIAN
	snprintf(buf, 18, "%d.%d.%d.%d", 
		 (int)p[0], (int)p[1], (int)p[2], (int)p[3]);
#else
	snprintf(buf, 18, "%d.%d.%d.%d", 
		 (int)p[3], (int)p[2], (int)p[1], (int)p[0]);
#endif
	return buf;
}
#endif

#ifdef REPLACE_INET_ATON
 int inet_aton(const char *cp, struct in_addr *inp)
{
	unsigned int a1, a2, a3, a4;
	unsigned long ret;

	if (strcmp(cp, "255.255.255.255") == 0) {
		inp->s_addr = (unsigned) -1;
		return 0;
	}

	if (sscanf(cp, "%u.%u.%u.%u", &a1, &a2, &a3, &a4) != 4 ||
	    a1 > 255 || a2 > 255 || a3 > 255 || a4 > 255) {
		return 0;
	}

	ret = (a1 << 24) | (a2 << 16) | (a3 << 8) | a4;

	inp->s_addr = htonl(ret);
	
	if (inp->s_addr == (unsigned) -1) {
		return 0;
	}
	return 1;
}
#endif

/* some systems don't take the 2nd argument */
int sys_gettimeofday(struct timeval *tv)
{
#if HAVE_GETTIMEOFDAY_TZ
	return gettimeofday(tv, NULL);
#else
	return gettimeofday(tv);
#endif
}

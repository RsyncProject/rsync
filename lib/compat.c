/* 
   Copyright (C) Andrew Tridgell 1998
   
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

/*
  compatibility functions - replacing functions for platforms that don't
  have them.

  */
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
	return wait4(pid, statptr, options, NULL);
}
#endif


#ifndef HAVE_MEMMOVE
void *memmove(void *dest, const void *src, size_t n)
{
	memcpy(dest, src, n);
	return dest;
}
#endif

#ifndef HAVE_STRPBRK
/* Find the first ocurrence in S of any character in ACCEPT.  
   derived from glibc 
*/
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

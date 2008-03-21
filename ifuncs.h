/* Inline functions for rsync.
 *
 * Copyright (C) 2007-2008 Wayne Davison
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

static inline void
alloc_xbuf(xbuf *xb, size_t sz)
{
	if (!(xb->buf = new_array(char, sz)))
		out_of_memory("alloc_xbuf");
	xb->size = sz;
	xb->len = xb->pos = 0;
}

static inline void
realloc_xbuf(xbuf *xb, size_t sz)
{
	char *bf = realloc_array(xb->buf, char, sz);
	if (!bf)
		out_of_memory("realloc_xbuf");
	xb->buf = bf;
	xb->size = sz;
}

static inline int
to_wire_mode(mode_t mode)
{
#ifdef SUPPORT_LINKS
#if _S_IFLNK != 0120000
	if (S_ISLNK(mode))
		return (mode & ~(_S_IFMT)) | 0120000;
#endif
#endif
	return mode;
}

static inline mode_t
from_wire_mode(int mode)
{
#if _S_IFLNK != 0120000
	if ((mode & (_S_IFMT)) == 0120000)
		return (mode & ~(_S_IFMT)) | _S_IFLNK;
#endif
	return mode;
}

static inline char *
d_name(struct dirent *di)
{
#ifdef HAVE_BROKEN_READDIR
	return (di->d_name - 2);
#else
	return di->d_name;
#endif
}

static inline int
isDigit(const char *ptr)
{
	return isdigit(*(unsigned char *)ptr);
}

static inline int
isPrint(const char *ptr)
{
	return isprint(*(unsigned char *)ptr);
}

static inline int
isSpace(const char *ptr)
{
	return isspace(*(unsigned char *)ptr);
}

static inline int
isLower(const char *ptr)
{
	return islower(*(unsigned char *)ptr);
}

static inline int
isUpper(const char *ptr)
{
	return isupper(*(unsigned char *)ptr);
}

static inline int
toLower(const char *ptr)
{
	return tolower(*(unsigned char *)ptr);
}

static inline int
toUpper(const char *ptr)
{
	return toupper(*(unsigned char *)ptr);
}

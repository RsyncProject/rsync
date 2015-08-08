/* Inline functions for rsync.
 *
 * Copyright (C) 2007-2015 Wayne Davison
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

static inline void
free_xbuf(xbuf *xb)
{
	if (xb->buf)
		free(xb->buf);
	memset(xb, 0, sizeof (xbuf));
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

static inline void
init_stat_x(stat_x *sx_p)
{
#ifdef SUPPORT_ACLS
	sx_p->acc_acl = sx_p->def_acl = NULL;
#endif
#ifdef SUPPORT_XATTRS
	sx_p->xattr = NULL;
#endif
}

static inline void
free_stat_x(stat_x *sx_p)
{
#ifdef SUPPORT_ACLS
    {
	extern int preserve_acls;
	if (preserve_acls)
		free_acl(sx_p);
    }
#endif
#ifdef SUPPORT_XATTRS
    {
	extern int preserve_xattrs;
	if (preserve_xattrs)
		free_xattr(sx_p);
    }
#endif
}

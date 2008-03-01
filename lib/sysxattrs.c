/*
 * Extended attribute support for rsync.
 *
 * Copyright (C) 2004 Red Hat, Inc.
 * Copyright (C) 2003-2008 Wayne Davison
 * Written by Jay Fenlason.
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
#include "sysxattrs.h"

#ifdef SUPPORT_XATTRS

#if defined HAVE_LINUX_XATTRS

ssize_t sys_lgetxattr(const char *path, const char *name, void *value, size_t size)
{
	return lgetxattr(path, name, value, size);
}

ssize_t sys_fgetxattr(int filedes, const char *name, void *value, size_t size)
{
	return fgetxattr(filedes, name, value, size);
}

int sys_lsetxattr(const char *path, const char *name, const void *value, size_t size)
{
	return lsetxattr(path, name, value, size, 0);
}

int sys_lremovexattr(const char *path, const char *name)
{
	return lremovexattr(path, name);
}

ssize_t sys_llistxattr(const char *path, char *list, size_t size)
{
	return llistxattr(path, list, size);
}

#elif HAVE_OSX_XATTRS

ssize_t sys_lgetxattr(const char *path, const char *name, void *value, size_t size)
{
	return getxattr(path, name, value, size, 0, XATTR_NOFOLLOW);
}

ssize_t sys_fgetxattr(int filedes, const char *name, void *value, size_t size)
{
	return fgetxattr(filedes, name, value, size, 0, 0);
}

int sys_lsetxattr(const char *path, const char *name, const void *value, size_t size)
{
	return setxattr(path, name, value, size, 0, XATTR_NOFOLLOW);
}

int sys_lremovexattr(const char *path, const char *name)
{
	return removexattr(path, name, XATTR_NOFOLLOW);
}

ssize_t sys_llistxattr(const char *path, char *list, size_t size)
{
	return listxattr(path, list, size, XATTR_NOFOLLOW);
}

#elif HAVE_FREEBSD_XATTRS

ssize_t sys_lgetxattr(const char *path, const char *name, void *value, size_t size)
{
	return extattr_get_link(path, EXTATTR_NAMESPACE_USER, name, value, size);
}

ssize_t sys_fgetxattr(int filedes, const char *name, void *value, size_t size)
{
	return extattr_get_fd(filedes, EXTATTR_NAMESPACE_USER, name, value, size);
}

int sys_lsetxattr(const char *path, const char *name, const void *value, size_t size)
{
	return extattr_set_link(path, EXTATTR_NAMESPACE_USER, name, value, size);
}

int sys_lremovexattr(const char *path, const char *name)
{
	return extattr_delete_link(path, EXTATTR_NAMESPACE_USER, name);
}

ssize_t sys_llistxattr(const char *path, char *list, size_t size)
{
	unsigned char keylen;
	ssize_t off, len = extattr_list_link(path, EXTATTR_NAMESPACE_USER, list, size);

	if (len <= 0 || (size_t)len > size)
		return len;

	/* FreeBSD puts a single-byte length before each string, with no '\0'
	 * terminator.  We need to change this into a series of null-terminted
	 * strings.  Since the size is the same, we can simply transform the
	 * output in place. */
	for (off = 0; off < len; off += keylen + 1) {
		keylen = ((unsigned char*)list)[off];
		if (off + keylen >= len) {
			/* Should be impossible, but kernel bugs happen! */
			errno = EINVAL;
			return -1;
		}
		memmove(list+off, list+off+1, keylen);
		list[off+keylen] = '\0';
	}

	return len;
}

#else

#error You need to create xattr compatibility functions.

#endif

#endif /* SUPPORT_XATTRS */

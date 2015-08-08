/*
 * Extended attribute support for rsync.
 *
 * Copyright (C) 2004 Red Hat, Inc.
 * Copyright (C) 2003-2015 Wayne Davison
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

#ifdef HAVE_OSX_XATTRS
#define GETXATTR_FETCH_LIMIT (64*1024*1024)
#endif

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
	ssize_t len = getxattr(path, name, value, size, 0, XATTR_NOFOLLOW);

	/* If we're retrieving data, handle resource forks > 64MB specially */
	if (value != NULL && len == GETXATTR_FETCH_LIMIT && (size_t)len < size) {
		/* getxattr will only return 64MB of data at a time, need to call again with a new offset */
		u_int32_t offset = len;
		size_t data_retrieved = len;
		while (data_retrieved < size) {
			len = getxattr(path, name, value + offset, size - data_retrieved, offset, XATTR_NOFOLLOW);
			if (len <= 0)
				break;
			data_retrieved += len;
			offset += (u_int32_t)len;
		}
		len = data_retrieved;
	}

	return len;
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

#elif HAVE_SOLARIS_XATTRS

static ssize_t read_xattr(int attrfd, void *buf, size_t buflen)
{
	STRUCT_STAT sb;
	ssize_t ret;

	if (fstat(attrfd, &sb) < 0)
		ret = -1;
	else if (sb.st_size > SSIZE_MAX) {
		errno = ERANGE;
		ret = -1;
	} else if (buflen == 0)
		ret = sb.st_size;
	else if (sb.st_size > buflen) {
		errno = ERANGE;
		ret = -1;
	} else {
		size_t bufpos;
		for (bufpos = 0; bufpos < sb.st_size; ) {
			ssize_t cnt = read(attrfd, buf + bufpos, sb.st_size - bufpos);
			if (cnt <= 0) {
				if (cnt < 0 && errno == EINTR)
					continue;
				bufpos = -1;
				break;
			}
			bufpos += cnt;
		}
		ret = bufpos;
	}

	close(attrfd);

	return ret;
}

ssize_t sys_lgetxattr(const char *path, const char *name, void *value, size_t size)
{
	int attrfd;

	if ((attrfd = attropen(path, name, O_RDONLY)) < 0) {
		errno = ENOATTR;
		return -1;
	}

	return read_xattr(attrfd, value, size);
}

ssize_t sys_fgetxattr(int filedes, const char *name, void *value, size_t size)
{
	int attrfd;

	if ((attrfd = openat(filedes, name, O_RDONLY|O_XATTR, 0)) < 0) {
		errno = ENOATTR;
		return -1;
	}

	return read_xattr(attrfd, value, size);
}

int sys_lsetxattr(const char *path, const char *name, const void *value, size_t size)
{
	int attrfd;
	size_t bufpos;
	mode_t mode = S_IRUSR | S_IWUSR | S_IRGRP | S_IWGRP;

	if ((attrfd = attropen(path, name, O_CREAT|O_TRUNC|O_WRONLY, mode)) < 0)
		return -1;

	for (bufpos = 0; bufpos < size; ) {
		ssize_t cnt = write(attrfd, value+bufpos, size);
		if (cnt <= 0) {
			if (cnt < 0 && errno == EINTR)
				continue;
			bufpos = -1;
			break;
		}
		bufpos += cnt;
	}

	close(attrfd);

	return bufpos > 0 ? 0 : -1;
}

int sys_lremovexattr(const char *path, const char *name)
{
	int attrdirfd;
	int ret;

	if ((attrdirfd = attropen(path, ".", O_RDONLY)) < 0)
		return -1;

	ret = unlinkat(attrdirfd, name, 0);

	close(attrdirfd);

	return ret;
}

ssize_t sys_llistxattr(const char *path, char *list, size_t size)
{
	int attrdirfd;
	DIR *dirp;
	struct dirent *dp;
	ssize_t ret = 0;

	if ((attrdirfd = attropen(path, ".", O_RDONLY)) < 0) {
		errno = ENOTSUP;
		return -1;
	}

	if ((dirp = fdopendir(attrdirfd)) == NULL) {
		close(attrdirfd);
		return -1;
	}

	while ((dp = readdir(dirp))) {
		int len = strlen(dp->d_name);

		if (dp->d_name[0] == '.' && (len == 1 || (len == 2 && dp->d_name[1] == '.')))
			continue;
		if (len == 11 && dp->d_name[0] == 'S' && strncmp(dp->d_name, "SUNWattr_r", 10) == 0
		 && (dp->d_name[10] == 'o' || dp->d_name[10] == 'w'))
			continue;

		if ((ret += len+1) > size) {
			if (size == 0)
				continue;
			ret = -1;
			errno = ERANGE;
			break;
		}
		memcpy(list, dp->d_name, len+1);
		list += len+1;
	}

	closedir(dirp);
	close(attrdirfd);

	return ret;
}

#else

#error You need to create xattr compatibility functions.

#endif

#endif /* SUPPORT_XATTRS */

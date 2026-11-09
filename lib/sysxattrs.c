/*
 * Extended attribute support for rsync.
 *
 * Copyright (C) 2004 Red Hat, Inc.
 * Copyright (C) 2003-2022 Wayne Davison
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

int sys_fsetxattr(int filedes, const char *name, const void *value, size_t size)
{
	return fsetxattr(filedes, name, value, size, 0);
}

int sys_lremovexattr(const char *path, const char *name)
{
	return lremovexattr(path, name);
}

int sys_fremovexattr(int filedes, const char *name)
{
	return fremovexattr(filedes, name);
}

ssize_t sys_llistxattr(const char *path, char *list, size_t size)
{
	return llistxattr(path, list, size);
}

ssize_t sys_flistxattr(int filedes, char *list, size_t size)
{
	return flistxattr(filedes, list, size);
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
			len = getxattr(path, name, (char*)value + offset, size - data_retrieved, offset, XATTR_NOFOLLOW);
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

int sys_fsetxattr(int filedes, const char *name, const void *value, size_t size)
{
	return fsetxattr(filedes, name, value, size, 0, 0);
}

int sys_lremovexattr(const char *path, const char *name)
{
	return removexattr(path, name, XATTR_NOFOLLOW);
}

int sys_fremovexattr(int filedes, const char *name)
{
	return fremovexattr(filedes, name, 0);
}

ssize_t sys_llistxattr(const char *path, char *list, size_t size)
{
	return listxattr(path, list, size, XATTR_NOFOLLOW);
}

ssize_t sys_flistxattr(int filedes, char *list, size_t size)
{
	return flistxattr(filedes, list, size, 0);
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

int sys_fsetxattr(int filedes, const char *name, const void *value, size_t size)
{
	return extattr_set_fd(filedes, EXTATTR_NAMESPACE_USER, name, value, size);
}

int sys_lremovexattr(const char *path, const char *name)
{
	return extattr_delete_link(path, EXTATTR_NAMESPACE_USER, name);
}

int sys_fremovexattr(int filedes, const char *name)
{
	return extattr_delete_fd(filedes, EXTATTR_NAMESPACE_USER, name);
}

/* Turn the FreeBSD extattr_list_xx() output (a single length byte before each
 * name, no '\0' terminator) into the series of null-terminated strings that the
 * rest of rsync expects.  Since the size is unchanged, transform in place.
 * Shared by the path and fd list variants. */
static ssize_t freebsd_list_finish(char *list, size_t size, ssize_t len)
{
	unsigned char keylen;
	ssize_t off;

	if (len <= 0 || size == 0)
		return len;

	if ((size_t)len >= size) {
		/* FreeBSD extattr_list_xx() returns 'size' as 'len' in case there are
		   more data available, truncating the output, we solve this by signalling
		   ERANGE in case len == size so that the code in xattrs.c will retry with
		   a bigger buffer */
		errno = ERANGE;
		return -1;
	}

	for (off = 0; off < len; off += keylen + 1) {
		keylen = ((unsigned char*)list)[off];
		if (off + keylen >= len) {
			/* Should be impossible, but bugs happen! */
			errno = EINVAL;
			return -1;
		}
		memmove(list+off, list+off+1, keylen);
		list[off+keylen] = '\0';
	}

	return len;
}

ssize_t sys_llistxattr(const char *path, char *list, size_t size)
{
	return freebsd_list_finish(list, size,
		extattr_list_link(path, EXTATTR_NAMESPACE_USER, list, size));
}

ssize_t sys_flistxattr(int filedes, char *list, size_t size)
{
	return freebsd_list_finish(list, size,
		extattr_list_fd(filedes, EXTATTR_NAMESPACE_USER, list, size));
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
			ssize_t cnt = read(attrfd, (char*)buf + bufpos, sb.st_size - bufpos);
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

/* Write a datum to the already-opened attribute fd, closing it.  Shared by the
 * path- and fd-keyed setters below. */
static int write_xattr(int attrfd, const void *value, size_t size)
{
	size_t bufpos;
	int ret = 0, saved_errno = 0;

	for (bufpos = 0; bufpos < size; ) {
		ssize_t cnt = write(attrfd, (const char *)value + bufpos, size - bufpos);
		if (cnt < 0) {
			if (errno == EINTR)
				continue;
			ret = -1;
			saved_errno = errno;
			break;
		}
		if (cnt == 0) {
			ret = -1;
			saved_errno = EIO;
			break;
		}
		bufpos += cnt;
	}

	/* Don't let close() clobber the write error; do report a close() failure. */
	if (close(attrfd) < 0 && ret == 0)
		return -1;
	if (ret < 0 && saved_errno)
		errno = saved_errno;

	return ret;
}

int sys_lsetxattr(const char *path, const char *name, const void *value, size_t size)
{
	int attrfd;
	mode_t mode = S_IRUSR | S_IWUSR | S_IRGRP | S_IWGRP;

	if ((attrfd = attropen(path, name, O_CREAT|O_TRUNC|O_WRONLY, mode)) < 0)
		return -1;

	return write_xattr(attrfd, value, size);
}

int sys_fsetxattr(int filedes, const char *name, const void *value, size_t size)
{
	int attrfd;
	mode_t mode = S_IRUSR | S_IWUSR | S_IRGRP | S_IWGRP;

	if ((attrfd = openat(filedes, name, O_CREAT|O_TRUNC|O_WRONLY|O_XATTR, mode)) < 0)
		return -1;

	return write_xattr(attrfd, value, size);
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

int sys_fremovexattr(int filedes, const char *name)
{
	int attrdirfd;
	int ret;

	if ((attrdirfd = openat(filedes, ".", O_RDONLY|O_XATTR, 0)) < 0)
		return -1;

	ret = unlinkat(attrdirfd, name, 0);

	close(attrdirfd);

	return ret;
}

/* List the names in an already-opened attribute-dir fd, consuming it.  Shared
 * by the path- and fd-keyed listers below. */
static ssize_t list_xattr(int attrdirfd, char *list, size_t size)
{
	DIR *dirp;
	struct dirent *dp;
	ssize_t ret = 0;

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

		ret += len + 1;
		if ((size_t)ret > size) {
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

	return ret;
}

ssize_t sys_llistxattr(const char *path, char *list, size_t size)
{
	int attrdirfd;

	if ((attrdirfd = attropen(path, ".", O_RDONLY)) < 0) {
		errno = ENOTSUP;
		return -1;
	}

	return list_xattr(attrdirfd, list, size);
}

ssize_t sys_flistxattr(int filedes, char *list, size_t size)
{
	int attrdirfd;

	if ((attrdirfd = openat(filedes, ".", O_RDONLY|O_XATTR, 0)) < 0) {
		errno = ENOTSUP;
		return -1;
	}

	return list_xattr(attrdirfd, list, size);
}

#else

#error You need to create xattr compatibility functions.

#endif

#endif /* SUPPORT_XATTRS */

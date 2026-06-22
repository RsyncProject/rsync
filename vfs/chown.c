/*
 * vfs/chown.c - lchown wrappers (path, parent-resolved, held-dirfd).
 *
 * Moved verbatim out of syscall.c.
 *
 * Copyright (C) 1998-2022 Andrew Tridgell, Martin Pool, Wayne Davison
 * Copyright (C) 2026 Wayne Davison, Andrew Tridgell
 *
 * This program is free software; you can redistribute it and/or modify
 * it under the terms of the GNU General Public License as published by
 * the Free Software Foundation; either version 3 of the License, or
 * (at your option) any later version.
 */

#include "rsync.h"
#include "ifuncs.h"
#include "vfs/vfs_internal.h"

#ifndef HAVE_LCHOWN
#define lchown chown
#endif

static int vfs__lchown_plain(const char *path, uid_t owner, gid_t group)
{
	return lchown(path, owner, group);
}

/* Secure receiver-side resolve: open the parent under vfs_resolve_open() and
 * fchownat(..., AT_SYMLINK_NOFOLLOW) so a parent-component symlink swap can't
 * redirect the chown outside the module.  Like vfs_chmod, lchown has no
 * ownership-walk branch (VFS_OPERATOR_PATH resolves the same as the default
 * secure walk).  Falls through to the plain lchown in non-daemon/sender,
 * chrooted, no-parent and absolute-path cases. */
static int vfs__lchown_secure(const char *path, uid_t owner, gid_t group, int flags)
{
	(void)flags;
#if defined AT_FDCWD && defined O_NOFOLLOW && defined O_DIRECTORY
	char dirpath[MAXPATHLEN];
	const char *bname, *slash;
	int dfd, ret, e;
	size_t dlen;

	if (!vfs_relpath_active() || !*path || *path == '/')
		return vfs__lchown_plain(path, owner, group);
	slash = strrchr(path, '/');
	if (!slash)
		return vfs__lchown_plain(path, owner, group);
	dlen = slash - path;
	if (dlen >= sizeof dirpath) {
		errno = ENAMETOOLONG;
		return -1;
	}
	memcpy(dirpath, path, dlen);
	dirpath[dlen] = '\0';
	bname = slash + 1;

	dfd = vfs_resolve_open(NULL, dirpath, O_RDONLY | O_DIRECTORY, 0);
	if (dfd < 0)
		return -1;
	ret = fchownat(dfd, bname, owner, group, AT_SYMLINK_NOFOLLOW);
	e = errno;
	close(dfd);
	errno = e;
	return ret;
#else
	return vfs__lchown_plain(path, owner, group);
#endif
}

/* Unified lchown.  dirfd == VFS_AT_FDCWD resolves `path`; a real held dirfd
 * makes `path` a single component chowned (no-follow) under it.  flags:
 * VFS_ALLOW_SYMLINK (trusted, plain lchown), default 0 (secure receiver
 * resolve). */
int vfs_lchown(int dirfd, const char *path, uid_t owner, gid_t group, int flags)
{
	if (dry_run) return 0;
	RETURN_ERROR_IF_RO_OR_LO;
	RETURN_ERROR_IF_NULL(path);

	if (dirfd != VFS_AT_FDCWD) {
#ifdef AT_FDCWD
		/* Held-fd: reject empty, multi-component and ".." (writing the
		 * parent of the pinned dir); "." (chown the dir itself) is a
		 * legitimate single-component op. */
		if (!*path || strchr(path, '/')
		 || (path[0] == '.' && path[1] == '.' && path[2] == '\0')) {
			errno = EINVAL;
			return -1;
		}
		return fchownat(dirfd, path, owner, group, AT_SYMLINK_NOFOLLOW);
#else
		(void)dirfd; (void)owner; (void)group;
		errno = ENOSYS;
		return -1;
#endif
	}

	if (flags & VFS_ALLOW_SYMLINK)
		return vfs__lchown_plain(path, owner, group);
	return vfs__lchown_secure(path, owner, group, flags);
}

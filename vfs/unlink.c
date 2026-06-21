/*
 * vfs/unlink.c - unlink and rmdir wrappers (path, parent-resolved, held-dirfd).
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

/* Secure receiver-side resolve for an unlink/rmdir.  unlink() resolves parent
 * components, so a parent-symlink swap can delete an outside file under the
 * daemon's authority -- defence is to resolve the parent securely and unlinkat()
 * the leaf.  unlink (not rmdir) honours the operator ownership walk; the rmdir
 * path has no owner-walk branch (pre-existing -- matched the old vfs_rmdir_at).
 * Falls through to a plain unlink()/rmdir() in non-daemon/sender, chrooted,
 * no-parent and absolute-path cases. */
static int vfs__unlink_secure(const char *path, int flags)
{
	int rmdir_op = flags & VFS_REMOVEDIR;
#if defined AT_FDCWD && defined O_NOFOLLOW && defined O_DIRECTORY
	char dirpath[MAXPATHLEN];
	const char *bname, *slash;
	int dfd, ret, e, atflag = rmdir_op ? AT_REMOVEDIR : 0;
	size_t dlen;

	if (!rmdir_op && (flags & VFS_OPERATOR_PATH)) {
		if (vfs_symlink_optout_allowed())
			return unlink(path);
		dfd = vfs_owner_walk_parent(path, &bname, 1);
		if (dfd < 0)
			return -1;
		ret = unlinkat(dfd, bname, 0);
		e = errno;
		close(dfd);
		errno = e;
		return ret;
	}

	if (!vfs_relpath_active() || !*path || *path == '/')
		return rmdir_op ? rmdir(path) : unlink(path);
	slash = strrchr(path, '/');
	if (!slash)
		return rmdir_op ? rmdir(path) : unlink(path);
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
	ret = unlinkat(dfd, bname, atflag);
	e = errno;
	close(dfd);
	errno = e;
	return ret;
#else
	return rmdir_op ? rmdir(path) : unlink(path);
#endif
}

/* Unified unlink/rmdir.  dirfd == VFS_AT_FDCWD resolves `path`; a real held
 * dirfd makes `path` a single component removed directly under it.  flags:
 * VFS_REMOVEDIR (rmdir/AT_REMOVEDIR instead of unlink), VFS_ALLOW_SYMLINK
 * (trusted, plain), VFS_OPERATOR_PATH (operator path; unlink only), default 0
 * (secure receiver resolve). */
int vfs_unlink(int dirfd, const char *path, int flags)
{
	if (dry_run) return 0;
	RETURN_ERROR_IF_RO_OR_LO;
	RETURN_ERROR_IF_NULL(path);

	if (dirfd != VFS_AT_FDCWD) {
#ifdef AT_FDCWD
		if (!*path || strchr(path, '/')
		 || (path[0] == '.' && (path[1] == '\0'
		     || (path[1] == '.' && path[2] == '\0')))) {
			errno = EINVAL;
			return -1;
		}
		return unlinkat(dirfd, path, (flags & VFS_REMOVEDIR) ? AT_REMOVEDIR : 0);
#else
		(void)dirfd;
		errno = ENOSYS;
		return -1;
#endif
	}

	if (flags & VFS_ALLOW_SYMLINK)
		return (flags & VFS_REMOVEDIR) ? rmdir(path) : unlink(path);

	return vfs__unlink_secure(path, flags);
}

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

int vfs_unlink(const char *path)
{
	if (dry_run) return 0;
	RETURN_ERROR_IF_RO_OR_LO;
	return unlink(path);
}

/*
  Symlink-race-safe variant of vfs_unlink() for receiver-side use. See
  the comment on vfs_chmod_at() for the threat model. unlink() resolves
  parent components, so a parent-symlink swap can delete an outside
  file under the daemon's authority. Defence: open the parent of path
  under vfs_resolve_open() and use unlinkat() (flags=0) against
  that dirfd.

  Falls through to vfs_unlink() for the same dry-run / non-daemon /
  chrooted / no-parent / absolute-path cases as the other wrappers.
*/
int vfs_unlink_at(const char *path)
{
#ifdef AT_FDCWD
	char dirpath[MAXPATHLEN];
	const char *bname;
	const char *slash;
	int dfd, ret, e;
	size_t dlen;

	if (dry_run) return 0;
	RETURN_ERROR_IF_RO_OR_LO;
	RETURN_ERROR_IF_NULL(path);

#if defined O_NOFOLLOW && defined O_DIRECTORY
	if (vfs.operator_path_resolve) {
		if (vfs_symlink_optout_allowed())
			return unlink(path);
		dfd = vfs_owner_walk_parent(path, &bname);
		if (dfd < 0)
			return -1;
		ret = unlinkat(dfd, bname, 0);
		e = errno;
		close(dfd);
		errno = e;
		return ret;
	}
#endif

	if (!vfs_relpath_active())
		return unlink(path);

	if (!path || !*path || *path == '/')
		return unlink(path);

	slash = strrchr(path, '/');
	if (!slash)
		return unlink(path);

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

	ret = unlinkat(dfd, bname, 0);
	e = errno;
	close(dfd);
	errno = e;
	return ret;
#else
	return vfs_unlink(path);
#endif
}

int vfs_rmdir(const char *pathname)
{
	if (dry_run) return 0;
	RETURN_ERROR_IF_RO_OR_LO;
	return rmdir(pathname);
}

/*
  Symlink-race-safe variant of vfs_rmdir(). See vfs_unlink_at() above;
  same shape but with AT_REMOVEDIR set to require the target be a
  directory.
*/
int vfs_rmdir_at(const char *pathname)
{
#ifdef AT_FDCWD
	char dirpath[MAXPATHLEN];
	const char *bname;
	const char *slash;
	int dfd, ret, e;
	size_t dlen;

	if (dry_run) return 0;
	RETURN_ERROR_IF_RO_OR_LO;
	RETURN_ERROR_IF_NULL(pathname);

	if (!vfs_relpath_active())
		return rmdir(pathname);

	if (!pathname || !*pathname || *pathname == '/')
		return rmdir(pathname);

	slash = strrchr(pathname, '/');
	if (!slash)
		return rmdir(pathname);

	dlen = slash - pathname;
	if (dlen >= sizeof dirpath) {
		errno = ENAMETOOLONG;
		return -1;
	}
	memcpy(dirpath, pathname, dlen);
	dirpath[dlen] = '\0';
	bname = slash + 1;

	dfd = vfs_resolve_open(NULL, dirpath, O_RDONLY | O_DIRECTORY, 0);
	if (dfd < 0)
		return -1;

	ret = unlinkat(dfd, bname, AT_REMOVEDIR);
	e = errno;
	close(dfd);
	errno = e;
	return ret;
#else
	return vfs_rmdir(pathname);
#endif
}

int vfs_unlink_atfd(int dfd, const char *name, int flags)
{
#ifdef AT_FDCWD
	if (dry_run) return 0;
	RETURN_ERROR_IF_RO_OR_LO;
	return unlinkat(dfd, name, flags);
#else
	(void)dfd; (void)name; (void)flags;
	errno = ENOSYS;
	return -1;
#endif
}

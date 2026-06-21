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

int vfs_lchown(const char *path, uid_t owner, gid_t group)
{
	if (dry_run) return 0;
	RETURN_ERROR_IF_RO_OR_LO;
	RETURN_ERROR_IF_NULL(path);
#ifndef HAVE_LCHOWN
#define lchown chown
#endif
	return lchown(path, owner, group);
}

/*
  Symlink-race-safe variant of vfs_lchown() for receiver-side use. See the
  comment on vfs_chmod_at() for the threat model and design rationale.

  Resolves the parent directory under vfs_resolve_open() and invokes
  fchownat(..., AT_SYMLINK_NOFOLLOW) against that dirfd, so that an
  attacker who substitutes a symlink into one of the parent components
  cannot redirect the chown outside the receiver's confinement. The
  AT_SYMLINK_NOFOLLOW flag matches lchown()'s "do not follow a final-
  component symlink" semantics.

  Falls through to vfs_lchown() in the dry-run / non-daemon / chrooted /
  absolute-path / no-parent cases, identical to vfs_chmod_at().
*/
int vfs_lchown_at(const char *fname, uid_t owner, gid_t group)
{
#ifdef AT_FDCWD
	char dirpath[MAXPATHLEN];
	const char *bname;
	const char *slash;
	int dfd, ret, e;
	size_t dlen;

	if (dry_run) return 0;
	RETURN_ERROR_IF_RO_OR_LO;

	if (!vfs_relpath_active())
		return vfs_lchown(fname, owner, group);

	if (!fname || !*fname || *fname == '/')
		return vfs_lchown(fname, owner, group);

	slash = strrchr(fname, '/');
	if (!slash)
		return vfs_lchown(fname, owner, group);

	dlen = slash - fname;
	if (dlen >= sizeof dirpath) {
		errno = ENAMETOOLONG;
		return -1;
	}
	memcpy(dirpath, fname, dlen);
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
	return vfs_lchown(fname, owner, group);
#endif
}

int vfs_lchown_atfd(int dfd, const char *name, uid_t owner, gid_t group)
{
#ifdef AT_FDCWD
	if (dry_run) return 0;
	RETURN_ERROR_IF_RO_OR_LO;
	return fchownat(dfd, name, owner, group, AT_SYMLINK_NOFOLLOW);
#else
	(void)dfd; (void)name; (void)owner; (void)group;
	errno = ENOSYS;
	return -1;
#endif
}

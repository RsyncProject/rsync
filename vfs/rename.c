/*
 * vfs/rename.c - rename wrappers (path, parent-resolved, held-dirfd).
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

int vfs_rename(const char *old_path, const char *new_path)
{
	if (dry_run) return 0;
	RETURN_ERROR_IF_RO_OR_LO;
	return rename(old_path, new_path);
}

/*
  Symlink-race-safe variant of vfs_rename() for receiver-side use. See
  the comment on vfs_chmod_at() for the threat model and design rationale.

  rename() is the central tmp -> final operation in rsync; if either the
  source or the destination has an attacker-substituted symlink in one
  of its parent components, the rename can publish or vanish files
  outside the module. Defence: open the parent of *each* path under
  vfs_resolve_open() and use renameat() against the resulting
  dirfds. When old_path and new_path share the same parent (the common
  case -- tmp file living next to its final name), we reuse the same
  dirfd for both sides.

  Falls through to vfs_rename() in dry-run, non-daemon, chrooted and
  absolute-path cases, identical to the other do_*_at() wrappers.
*/
int vfs_rename_at(const char *old_path, const char *new_path)
{
#ifdef AT_FDCWD
	char old_dirpath[MAXPATHLEN], new_dirpath[MAXPATHLEN];
	const char *old_bname, *new_bname;
	const char *old_slash, *new_slash;
	int old_dfd = AT_FDCWD, new_dfd = AT_FDCWD;
	BOOL old_owns = False, new_owns = False;
	int ret = -1, e;
	size_t old_dlen = 0, new_dlen = 0;

	if (dry_run) return 0;
	RETURN_ERROR_IF_RO_OR_LO;

	if (!vfs_relpath_active())
		return vfs_rename(old_path, new_path);

	if (!old_path || !*old_path || !new_path || !*new_path)
		return vfs_rename(old_path, new_path);

#if defined O_NOFOLLOW && defined O_DIRECTORY
	/* Operator-supplied path (e.g. a --backup-dir destination or a --temp-dir
	 * source): resolve each side's parent via the ownership walk (follow
	 * uid0/euid symlinks, refuse others; absolute and relative alike). */
	if (vfs.operator_path_resolve) {
		if (vfs_symlink_optout_allowed())
			return vfs_rename(old_path, new_path);
		old_dfd = vfs_owner_walk_parent(old_path, &old_bname, vfs.operator_path_resolve);
		if (old_dfd < 0)
			return -1;
		new_dfd = vfs_owner_walk_parent(new_path, &new_bname, vfs.operator_path_resolve);
		if (new_dfd < 0) {
			e = errno;
			close(old_dfd);
			errno = e;
			return -1;
		}
		ret = renameat(old_dfd, old_bname, new_dfd, new_bname);
		e = errno;
		close(new_dfd);
		close(old_dfd);
		errno = e;
		return ret;
	}
#endif

	old_slash = strrchr(old_path, '/');
	new_slash = strrchr(new_path, '/');

	/* An absolute path uses AT_FDCWD with the full path; only a *relative* side
	 * is confined under the secure resolver.  Confine each side independently:
	 * an absolute source (e.g. an absolute --temp-dir temp file) must NOT
	 * disable confinement of a relative destination, or finish_transfer's
	 * tmp->final rename re-resolves the dest from the path and a flipped parent
	 * symlink writes the file outside the tree (a symlink-race write escape). */
	if (*old_path == '/') {
		old_bname = old_path;
	} else if (old_slash) {
		old_dlen = old_slash - old_path;
		if (old_dlen >= sizeof old_dirpath) {
			errno = ENAMETOOLONG;
			return -1;
		}
		memcpy(old_dirpath, old_path, old_dlen);
		old_dirpath[old_dlen] = '\0';
		old_bname = old_slash + 1;
		old_dfd = vfs_resolve_open(NULL, old_dirpath, O_RDONLY | O_DIRECTORY, 0);
		if (old_dfd < 0)
			return -1;
		old_owns = True;
	} else {
		old_bname = old_path;
	}

	if (*new_path == '/') {
		new_bname = new_path;
	} else if (new_slash) {
		new_dlen = new_slash - new_path;
		if (new_dlen >= sizeof new_dirpath) {
			e = ENAMETOOLONG;
			if (old_owns) close(old_dfd);
			errno = e;
			return -1;
		}
		memcpy(new_dirpath, new_path, new_dlen);
		new_dirpath[new_dlen] = '\0';
		new_bname = new_slash + 1;
		if (old_owns && old_dlen == new_dlen
		 && memcmp(old_dirpath, new_dirpath, old_dlen) == 0) {
			new_dfd = old_dfd;
		} else {
			new_dfd = vfs_resolve_open(NULL, new_dirpath, O_RDONLY | O_DIRECTORY, 0);
			if (new_dfd < 0) {
				e = errno;
				if (old_owns) close(old_dfd);
				errno = e;
				return -1;
			}
			new_owns = True;
		}
	} else {
		new_bname = new_path;
	}

	ret = renameat(old_dfd, old_bname, new_dfd, new_bname);
	e = errno;
	if (new_owns)
		close(new_dfd);
	if (old_owns)
		close(old_dfd);
	errno = e;
	return ret;
#else
	return vfs_rename(old_path, new_path);
#endif
}

int vfs_rename_atfd(int old_dfd, const char *old_name, int new_dfd, const char *new_name)
{
#ifdef AT_FDCWD
	if (dry_run) return 0;
	RETURN_ERROR_IF_RO_OR_LO;
	return renameat(old_dfd, old_name, new_dfd, new_name);
#else
	(void)old_dfd; (void)old_name; (void)new_dfd; (void)new_name;
	errno = ENOSYS;
	return -1;
#endif
}

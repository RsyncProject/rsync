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
  the comment on vfs__chmod_secure() for the threat model and design rationale.

  rename() is the central tmp -> final operation in rsync; if either the
  source or the destination has an attacker-substituted symlink in one
  of its parent components, the rename can publish or vanish files
  outside the module. Defence: resolve the parent of *each* path under its
  OWN policy and renameat() between the resulting dirfds.

  old_flags / new_flags are the per-operand policy (VFS_OPERATOR_PATH for an
  operator-supplied operand -- a --backup-dir/--partial-dir/--temp-dir path; 0
  for a transfer path).  Passing them separately means an operator operand on one
  side cannot relax (owner-walk) the transfer-path confinement of the other side
  -- see vfs_twopath_side() for the per-side resolution.

  Falls through to vfs_rename() in dry-run, non-daemon, chrooted and
  --insecure-links cases, identical to the other *_at() wrappers.
*/
int vfs_rename_at(const char *old_path, const char *new_path, int old_flags, int new_flags)
{
#ifdef AT_FDCWD
	char old_dirpath[MAXPATHLEN], new_dirpath[MAXPATHLEN];
	const char *old_bname, *new_bname;
	int old_dfd = AT_FDCWD, new_dfd = AT_FDCWD;
	BOOL old_owns = False, new_owns = False;
	int ret, e;

	if (dry_run) return 0;
	RETURN_ERROR_IF_RO_OR_LO;

	if (!vfs_relpath_active())
		return vfs_rename(old_path, new_path);

	if (!old_path || !*old_path || !new_path || !*new_path)
		return vfs_rename(old_path, new_path);

	if (vfs_twopath_side(old_path, old_flags, &old_bname, &old_dfd, &old_owns,
			     old_dirpath, sizeof old_dirpath) < 0)
		return -1;
	if (vfs_twopath_side(new_path, new_flags, &new_bname, &new_dfd, &new_owns,
			     new_dirpath, sizeof new_dirpath) < 0) {
		e = errno;
		if (old_owns) close(old_dfd);
		errno = e;
		return -1;
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
	(void)old_flags; (void)new_flags;
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

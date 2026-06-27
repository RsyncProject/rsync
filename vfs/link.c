/*
 * vfs/link.c - hard-link wrappers (path, parent-resolved, held-dirfd).
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

#if defined HAVE_LINK || defined HAVE_LINKAT
int vfs_link(const char *old_path, const char *new_path)
{
	if (dry_run) return 0;
	RETURN_ERROR_IF_RO_OR_LO;
	RETURN_ERROR_IF_NULL(old_path);
	RETURN_ERROR_IF_NULL(new_path);
#ifdef HAVE_LINKAT
	return linkat(AT_FDCWD, old_path, AT_FDCWD, new_path, 0);
#else
	return link(old_path, new_path);
#endif
}

/*
  Symlink-race-safe variant of vfs_link() for receiver-side use. See
  the comment on vfs__chmod_secure() for the threat model. link() resolves
  parent components of *both* old_path and new_path, so a parent-
  symlink swap on either side can plant the new hard link outside
  the module, or hard-link an outside file into the module (read
  disclosure).

  Defence: resolve each path's parent under its OWN policy and linkat()
  between the two dirfds.  old_flags / new_flags are the per-operand policy
  (VFS_OPERATOR_PATH for an operator operand -- e.g. a --link-dest/--backup-dir
  basis; 0 for a transfer path), so an operator basis on one side can't relax the
  transfer-path confinement of the other -- see vfs_twopath_side().  flags=0 to
  linkat() matches the existing vfs_link() (don't follow a symlink old_path).
  Only available on systems with linkat(); pre-AT_FDCWD systems fall through.
*/
int vfs_link_at(const char *old_path, const char *new_path, int old_flags, int new_flags)
{
#if defined AT_FDCWD && defined HAVE_LINKAT
	char old_dirpath[MAXPATHLEN], new_dirpath[MAXPATHLEN];
	const char *old_bname, *new_bname;
	int old_dfd = AT_FDCWD, new_dfd = AT_FDCWD;
	BOOL old_owns = False, new_owns = False;
	int ret, e;

	if (dry_run) return 0;
	RETURN_ERROR_IF_RO_OR_LO;

	if (!vfs_relpath_active())
		return vfs_link(old_path, new_path);

	if (!old_path || !*old_path || !new_path || !*new_path)
		return vfs_link(old_path, new_path);

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

	ret = linkat(old_dfd, old_bname, new_dfd, new_bname, 0);
	e = errno;
	if (new_owns)
		close(new_dfd);
	if (old_owns)
		close(old_dfd);
	errno = e;
	return ret;
#else
	(void)old_flags; (void)new_flags;
	return vfs_link(old_path, new_path);
#endif
}
#endif


#if defined HAVE_LINK || defined HAVE_LINKAT
int vfs_link_atfd(int old_dfd, const char *old_name, int new_dfd, const char *new_name, int flags)
{
#if defined AT_FDCWD && defined HAVE_LINKAT
	if (dry_run) return 0;
	RETURN_ERROR_IF_RO_OR_LO;
	return linkat(old_dfd, old_name, new_dfd, new_name, flags);
#else
	(void)old_dfd; (void)old_name; (void)new_dfd; (void)new_name; (void)flags;
	errno = ENOSYS;
	return -1;
#endif
}
#endif

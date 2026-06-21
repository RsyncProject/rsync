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
  the comment on vfs_chmod_at() for the threat model. link() resolves
  parent components of *both* old_path and new_path, so a parent-
  symlink swap on either side can plant the new hard link outside
  the module, or hard-link an outside file into the module (read
  disclosure).

  Defence: open each parent under vfs_resolve_open() and use
  linkat() between the two dirfds, reusing one when the parents
  match. flags=0 matches the existing vfs_link() (don't follow a
  symbolic-link old_path). Only available on systems with linkat();
  pre-AT_FDCWD systems fall through to vfs_link().
*/
int vfs_link_at(const char *old_path, const char *new_path)
{
#if defined AT_FDCWD && defined HAVE_LINKAT
	char old_dirpath[MAXPATHLEN], new_dirpath[MAXPATHLEN];
	const char *old_bname, *new_bname;
	const char *old_slash, *new_slash;
	int old_dfd = AT_FDCWD, new_dfd = AT_FDCWD;
	BOOL old_owns = False, new_owns = False;
	int ret, e;
	size_t old_dlen = 0, new_dlen = 0;

	if (dry_run) return 0;
	RETURN_ERROR_IF_RO_OR_LO;

	if (!vfs_relpath_active())
		return vfs_link(old_path, new_path);

	if (!old_path || !*old_path || !new_path || !*new_path)
		return vfs_link(old_path, new_path);

#if defined O_NOFOLLOW && defined O_DIRECTORY
	/* Operator-supplied path (a --backup-dir/--link-dest side): resolve each
	 * parent via the ownership walk (follow uid0/euid symlinks, refuse others). */
	if (vfs.operator_path_resolve) {
		if (vfs_symlink_optout_allowed())
			return vfs_link(old_path, new_path);
		old_dfd = vfs_owner_walk_parent(old_path, &old_bname);
		if (old_dfd < 0)
			return -1;
		new_dfd = vfs_owner_walk_parent(new_path, &new_bname);
		if (new_dfd < 0) {
			e = errno;
			close(old_dfd);
			errno = e;
			return -1;
		}
		ret = linkat(old_dfd, old_bname, new_dfd, new_bname, 0);
		e = errno;
		close(new_dfd);
		close(old_dfd);
		errno = e;
		return ret;
	}
#endif

	old_slash = strrchr(old_path, '/');
	new_slash = strrchr(new_path, '/');

	/* Resolve each path's parent dir independently. A path without a
	 * slash lives in CWD (AT_FDCWD), no parent open required. A path
	 * with a slash needs vfs_resolve_open to confine its parent
	 * resolution -- otherwise a parent symlink (e.g. --link-dest=cd
	 * where cd -> /outside) lets the kernel-level linkat(AT_FDCWD,
	 * "cd/target.txt", ...) escape the module.  An absolute path uses
	 * AT_FDCWD + the full path; each side is confined independently, so an
	 * absolute source (e.g. an absolute --link-dest) cannot disable
	 * confinement of a relative destination. */
	if (*old_path == '/') {
		old_bname = old_path;
	} else if (old_slash) {
		old_dlen = old_slash - old_path;
		if (old_dlen >= sizeof old_dirpath) { errno = ENAMETOOLONG; return -1; }
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

	ret = linkat(old_dfd, old_bname, new_dfd, new_bname, 0);
	e = errno;
	if (new_owns)
		close(new_dfd);
	if (old_owns)
		close(old_dfd);
	errno = e;
	return ret;
#else
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

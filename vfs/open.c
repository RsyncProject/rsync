/*
 * vfs/open.c - open wrappers (path, parent-resolved, held-dirfd, nofollow).
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

int vfs_open(const char *pathname, int flags, mode_t mode)
{
	RETURN_ERROR_IF_NULL(pathname);
	if (flags != O_RDONLY) {
		RETURN_ERROR_IF(dry_run, 0);
		RETURN_ERROR_IF_RO_OR_LO;
	}

#ifdef O_NOATIME
	if (open_noatime)
		flags |= O_NOATIME;
#endif

	return open(pathname, flags | O_BINARY, mode);
}

/*
  Symlink-race-safe variant of vfs_open() for receiver-side use. See
  the comment on do_chmod_at() for the threat model. open() resolves
  parent components, so a parent-symlink swap can redirect the open
  to a file outside the module. This wrapper is defence-in-depth for
  bare-path vfs_open() sites that callers know are otherwise
  protected by secure parent-syscalls (e.g. generator.c's in-place
  backup creation, where robust_unlink() rejects the symlinked
  parent before this open is reached): if any of those upstream
  protections is later removed or regresses, the open here still
  refuses to escape the module.

  Defence: open the parent of pathname under vfs_resolve_open()
  and call openat() against the resulting dirfd with O_NOFOLLOW
  (so the basename itself isn't followed if it happens to be a
  pre-planted symlink, which is what we want for O_CREAT|O_EXCL).
*/
int vfs_open_at(const char *pathname, int flags, mode_t mode)
{
#ifdef AT_FDCWD
	char dirpath[MAXPATHLEN];
	const char *bname;
	const char *slash;
	int dfd, ret, e;
	size_t dlen;

	if (flags != O_RDONLY) {
		RETURN_ERROR_IF(dry_run, 0);
		RETURN_ERROR_IF_RO_OR_LO;
	}

#if defined O_NOFOLLOW && defined O_DIRECTORY
	if (vfs.operator_path_resolve) {
		if (vfs_symlink_optout_allowed())
			return vfs_open(pathname, flags, mode);
		dfd = vfs_owner_walk_parent(pathname, &bname);
		if (dfd < 0)
			return -1;
		ret = openat(dfd, bname, flags | O_NOFOLLOW, mode);
		e = errno;
		close(dfd);
		errno = e;
		return ret;
	}
#endif

	if (!vfs_relpath_active())
		return vfs_open(pathname, flags, mode);

	if (!pathname || !*pathname || *pathname == '/')
		return vfs_open(pathname, flags, mode);

	slash = strrchr(pathname, '/');
	if (!slash)
		return vfs_open(pathname, flags, mode);

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

#ifdef O_NOATIME
	if (open_noatime)
		flags |= O_NOATIME;
#endif

	ret = openat(dfd, bname, flags | O_NOFOLLOW | O_BINARY, mode);
	e = errno;
	close(dfd);
	errno = e;
	return ret;
#else
	return vfs_open(pathname, flags, mode);
#endif
}

int vfs_open_nofollow(const char *pathname, int flags)
{
#ifndef O_NOFOLLOW
	STRUCT_STAT f_st, l_st;
#endif
	int fd;

	if (flags != O_RDONLY) {
		RETURN_ERROR_IF(dry_run, 0);
		RETURN_ERROR_IF_RO_OR_LO;
#ifndef O_NOFOLLOW
		/* This function doesn't support write attempts w/o O_NOFOLLOW. */
		errno = EINVAL;
		return -1;
#endif
	}

#ifdef O_NOATIME
	if (open_noatime)
		flags |= O_NOATIME;
#endif

#ifdef O_NOFOLLOW
	fd = open(pathname, flags|O_NOFOLLOW);
#else
	if (vfs_lstat(pathname, &l_st) < 0)
		return -1;
	if (S_ISLNK(l_st.st_mode)) {
		errno = ELOOP;
		return -1;
	}
	if ((fd = open(pathname, flags)) < 0)
		return fd;
	if (vfs_fstat(fd, &f_st) < 0) {
	  close_and_return_error:
		{
			int save_errno = errno;
			close(fd);
			errno = save_errno;
		}
		return -1;
	}
	if (l_st.st_dev != f_st.st_dev || l_st.st_ino != f_st.st_ino) {
		errno = EINVAL;
		goto close_and_return_error;
	}
#endif

	return fd;
}

/*
  varient of vfs_open/vfs_open_nofollow which does vfs_open() if the
  copy_links or copy_unsafe_links options are set and does
  vfs_open_nofollow() otherwise

  This is used to prevent a race condition where an attacker could be
  switching a file between being a symlink and being a normal file

  The open is always done with O_RDONLY flags
 */
int vfs_open_checklinks(const char *pathname)
{
	if (copy_links || copy_unsafe_links) {
		return vfs_open(pathname, O_RDONLY, 0);
	}
	return vfs_open_nofollow(pathname, O_RDONLY);
}

int vfs_open_atfd(int dfd, const char *name, int flags, mode_t mode)
{
#ifdef AT_FDCWD
	if (flags != O_RDONLY) {
		RETURN_ERROR_IF(dry_run, 0);
		RETURN_ERROR_IF_RO_OR_LO;
	}
#ifdef O_NOATIME
	if (open_noatime)
		flags |= O_NOATIME;
#endif
	return openat(dfd, name, flags | O_NOFOLLOW | O_BINARY, mode);
#else
	(void)dfd; (void)name; (void)flags; (void)mode;
	errno = ENOSYS;
	return -1;
#endif
}

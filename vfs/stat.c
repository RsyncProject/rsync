/*
 * vfs/stat.c - stat/lstat/fstat wrappers (path, parent-resolved, held-dirfd).
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

int vfs_stat(const char *path, STRUCT_STAT *st)
{
	RETURN_ERROR_IF_NULL(path);
#ifdef USE_STAT64_FUNCS
	return stat64(path, st);
#else
	return stat(path, st);
#endif
}

int vfs_lstat(const char *path, STRUCT_STAT *st)
{
	RETURN_ERROR_IF_NULL(path);
#ifdef SUPPORT_LINKS
# ifdef USE_STAT64_FUNCS
	return lstat64(path, st);
# else
	return lstat(path, st);
# endif
#else
	return vfs_stat(path, st);
#endif
}

/*
  Symlink-race-safe variants of vfs_stat() / vfs_lstat() for receiver-
  side use. See the comment on do_chmod_at() for the threat model.
  stat() and lstat() resolve parent components, so a parent-symlink
  swap can make the receiver's stat see attributes of a victim file
  outside the module -- which then drives later behaviour (e.g.
  "this isn't a directory, delete it" -> attacker-controlled unlink
  on something outside the module).

  Defence: open the parent under vfs_resolve_open() and use
  fstatat() with AT_SYMLINK_NOFOLLOW (lstat) or 0 (stat) against
  that dirfd. Same fall-through gating as the other wrappers.
*/
static int do_xstat_at(const char *path, STRUCT_STAT *st, int at_flags, int (*fallback)(const char *, STRUCT_STAT *))
{
#ifdef AT_FDCWD
	char dirpath[MAXPATHLEN];
	const char *bname;
	const char *slash;
	int dfd, ret, e;
	size_t dlen;

#if defined O_NOFOLLOW && defined O_DIRECTORY
	if (vfs.operator_path_resolve) {
		if (vfs_symlink_optout_allowed())
			return fallback(path, st);
		dfd = vfs_owner_walk_parent(path, &bname);
		if (dfd < 0)
			return -1;
		ret = fstatat(dfd, bname, st, at_flags);
		e = errno;
		close(dfd);
		errno = e;
		return ret;
	}
#endif

	if (!vfs_relpath_active())
		return fallback(path, st);

	if (!path || !*path || *path == '/')
		return fallback(path, st);

	slash = strrchr(path, '/');
	if (!slash)
		return fallback(path, st);

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

	ret = fstatat(dfd, bname, st, at_flags);
	e = errno;
	close(dfd);
	errno = e;
	return ret;
#else
	return fallback(path, st);
#endif
}

int vfs_stat_at(const char *path, STRUCT_STAT *st)
{
	return do_xstat_at(path, st, 0, vfs_stat);
}

int vfs_lstat_at(const char *path, STRUCT_STAT *st)
{
#ifdef SUPPORT_LINKS
	return do_xstat_at(path, st, AT_SYMLINK_NOFOLLOW, vfs_lstat);
#else
	return do_xstat_at(path, st, 0, vfs_stat);
#endif
}

int vfs_fstat(int fd, STRUCT_STAT *st)
{
#ifdef USE_STAT64_FUNCS
	return fstat64(fd, st);
#else
	return fstat(fd, st);
#endif
}

int vfs_lstat_atfd(int dfd, const char *name, STRUCT_STAT *st)
{
#ifdef AT_FDCWD
# ifdef SUPPORT_LINKS
	return fstatat(dfd, name, st, AT_SYMLINK_NOFOLLOW);
# else
	return fstatat(dfd, name, st, 0);
# endif
#else
	(void)dfd; (void)name; (void)st;
	errno = ENOSYS;
	return -1;
#endif
}

int vfs_stat_atfd(int dfd, const char *name, STRUCT_STAT *st)
{
#ifdef AT_FDCWD
	return fstatat(dfd, name, st, 0);
#else
	(void)dfd; (void)name; (void)st;
	errno = ENOSYS;
	return -1;
#endif
}

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

static int vfs__stat_plain(const char *path, STRUCT_STAT *st)
{
	RETURN_ERROR_IF_NULL(path);
#ifdef USE_STAT64_FUNCS
	return stat64(path, st);
#else
	return stat(path, st);
#endif
}

static int vfs__lstat_plain(const char *path, STRUCT_STAT *st)
{
	RETURN_ERROR_IF_NULL(path);
#ifdef SUPPORT_LINKS
# ifdef USE_STAT64_FUNCS
	return lstat64(path, st);
# else
	return lstat(path, st);
# endif
#else
	return vfs__stat_plain(path, st);
#endif
}

/*
  Symlink-race-safe variants of vfs_stat() / vfs_lstat() for receiver-
  side use. See the comment on vfs__chmod_secure() for the threat model.
  stat() and lstat() resolve parent components, so a parent-symlink
  swap can make the receiver's stat see attributes of a victim file
  outside the module -- which then drives later behaviour (e.g.
  "this isn't a directory, delete it" -> attacker-controlled unlink
  on something outside the module).

  Defence: open the parent under vfs_resolve_open() and use
  fstatat() with AT_SYMLINK_NOFOLLOW (lstat) or 0 (stat) against
  that dirfd. Same fall-through gating as the other wrappers.
*/
static int do_xstat_at(const char *path, STRUCT_STAT *st, int at_flags, int (*fallback)(const char *, STRUCT_STAT *), int vfs_flags)
{
#ifdef AT_FDCWD
	char dirpath[MAXPATHLEN];
	const char *bname;
	const char *slash;
	int dfd, ret, e;
	size_t dlen;

#if defined O_NOFOLLOW && defined O_DIRECTORY
	if (vfs_flags & VFS_OPERATOR_PATH) {
		if (vfs_symlink_optout_allowed())
			return fallback(path, st);
		dfd = vfs_owner_walk_parent(path, &bname, 1);
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

/* Unified stat/lstat.  dirfd == VFS_AT_FDCWD resolves `path` (VFS_ALLOW_SYMLINK =
 * plain libc stat/lstat, default 0 = secure receiver resolve, VFS_OPERATOR_PATH =
 * ownership walk); a real held dirfd fstatat()s a single validated component
 * under it.  vfs_stat follows the leaf, vfs_lstat does not. */
int vfs_stat(int dirfd, const char *path, STRUCT_STAT *st, int flags)
{
	RETURN_ERROR_IF_NULL(path);
	if (dirfd != VFS_AT_FDCWD) {
#ifdef AT_FDCWD
		/* Held-fd: reject empty, multi-component (a '/' would resolve a path
		 * under the pinned dir) and ".." (escapes to the parent).  "." is
		 * allowed: a read-only fstatat of the dir itself is legitimate
		 * (link_stat_at). */
		if (!*path || strchr(path, '/')
		 || (path[0] == '.' && path[1] == '.' && path[2] == '\0')) {
			errno = EINVAL;
			return -1;
		}
		return fstatat(dirfd, path, st, 0);
#else
		(void)dirfd; errno = ENOSYS; return -1;
#endif
	}
	if (flags & VFS_ALLOW_SYMLINK)
		return vfs__stat_plain(path, st);
	return do_xstat_at(path, st, 0, vfs__stat_plain, flags);
}

int vfs_lstat(int dirfd, const char *path, STRUCT_STAT *st, int flags)
{
	RETURN_ERROR_IF_NULL(path);
	if (dirfd != VFS_AT_FDCWD) {
#ifdef AT_FDCWD
		/* Held-fd: reject empty, multi-component and ".."; "." is allowed
		 * (read-only fstatat of the dir itself -- link_stat_at). */
		if (!*path || strchr(path, '/')
		 || (path[0] == '.' && path[1] == '.' && path[2] == '\0')) {
			errno = EINVAL;
			return -1;
		}
# ifdef SUPPORT_LINKS
		return fstatat(dirfd, path, st, AT_SYMLINK_NOFOLLOW);
# else
		return fstatat(dirfd, path, st, 0);
# endif
#else
		(void)dirfd; errno = ENOSYS; return -1;
#endif
	}
	if (flags & VFS_ALLOW_SYMLINK)
		return vfs__lstat_plain(path, st);
#ifdef SUPPORT_LINKS
	return do_xstat_at(path, st, AT_SYMLINK_NOFOLLOW, vfs__lstat_plain, flags);
#else
	return do_xstat_at(path, st, 0, vfs__stat_plain, flags);
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


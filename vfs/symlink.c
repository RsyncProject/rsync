/*
 * vfs/symlink.c - symlink and readlink wrappers.
 *
 * Includes the fake-super (NO_SYMLINK_*XATTRS) placeholder-file handling that
 * stands in for real symlinks when the receiver can't create them.  Moved
 * verbatim out of syscall.c.
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

#ifdef SUPPORT_LINKS
static int vfs__symlink_plain(const char *lnk, const char *path)
{
	if (dry_run) return 0;
	RETURN_ERROR_IF_RO_OR_LO;
	RETURN_ERROR_IF_NULL(lnk);
	RETURN_ERROR_IF_NULL(path);

#if defined NO_SYMLINK_XATTRS || defined NO_SYMLINK_USER_XATTRS
	/* For --fake-super, we create a normal file with mode 0600
	 * and write the lnk into it. */
	if (am_root < 0) {
		int ok, len = strlen(lnk);
		int fd = open(path, O_WRONLY|O_CREAT|O_TRUNC, S_IWUSR|S_IRUSR);
		if (fd < 0)
			return -1;
		ok = write(fd, lnk, len) == len;
		if (close(fd) < 0)
			ok = 0;
		return ok ? 0 : -1;
	}
#endif

	return symlink(lnk, path);
}

/*
  Symlink-race-safe variant of vfs_symlink() for receiver-side use. See
  the comment on vfs_chmod_at() for the threat model. For a real symlink
  only the parent directory of `path` needs protection -- symlinkat()
  does not resolve the final component (it creates it). Defence: open
  the parent of `path` under vfs_resolve_open() and call symlinkat()
  against that dirfd; a top-level (no-slash) path has no parent to
  confine, so it uses AT_FDCWD directly. The link target string `lnk` is
  stored verbatim and not resolved at creation time, so it doesn't need
  scrutiny here.

  For --fake-super (am_root < 0) the "symlink" is written as a regular
  file, so the final component IS resolved at creation: we create it
  with openat(... O_NOFOLLOW) so a pre-planted symlink at the basename
  cannot redirect the write outside the module. This protection applies
  to top-level paths too -- the previous code fell through to the
  bare-path vfs_symlink() there, whose plain open() followed such a
  symlink.
*/
/* NOTE: unlike vfs_mkdir/vfs_mknod, the symlink secure path has no ownership-walk
 * branch -- VFS_OPERATOR_PATH is accepted but resolves the same as the default
 * secure receiver walk (the link target is stored verbatim and never resolved at
 * creation; only the parent dir is confined).  Pre-existing asymmetry. */
static int vfs__symlink_secure(const char *lnk, const char *path, int flags)
{
	(void)flags;
#ifdef AT_FDCWD
	char dirpath[MAXPATHLEN];
	const char *bname;
	const char *slash;
	int dfd = AT_FDCWD, ret, e;
	BOOL owns = False;
	size_t dlen;

	if (dry_run) return 0;
	RETURN_ERROR_IF_RO_OR_LO;

	if (!vfs_relpath_active())
		return vfs__symlink_plain(lnk, path);

	if (!path || !*path || *path == '/')
		return vfs__symlink_plain(lnk, path);

	/* A path with a slash needs vfs_resolve_open to confine its parent;
	 * a top-level path is in CWD (AT_FDCWD), no parent to subvert.  The leaf
	 * is protected below either way (symlinkat() won't follow it; the
	 * fake-super openat() uses O_NOFOLLOW). */
	slash = strrchr(path, '/');
	if (slash) {
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
		owns = True;
	} else {
		bname = path;
	}

#if defined NO_SYMLINK_XATTRS || defined NO_SYMLINK_USER_XATTRS
	/* For --fake-super, vfs_symlink writes the link target into a
	 * regular file rather than creating a real symlink. Do that here
	 * against the (secure or AT_FDCWD) dirfd, with O_NOFOLLOW so a pre-
	 * planted symlink at the basename can't redirect the file creation. */
	if (am_root < 0) {
		int len = strlen(lnk);
		int fd = openat(dfd, bname,
				O_WRONLY | O_CREAT | O_TRUNC | O_NOFOLLOW,
				S_IWUSR | S_IRUSR);
		if (fd < 0) {
			e = errno;
			if (owns) close(dfd);
			errno = e;
			return -1;
		}
		ret = (write(fd, lnk, len) == len) ? 0 : -1;
		if (close(fd) < 0)
			ret = -1;
		e = errno;
		if (owns) close(dfd);
		errno = e;
		return ret;
	}
#endif

	ret = symlinkat(lnk, dfd, bname);
	e = errno;
	if (owns) close(dfd);
	errno = e;
	return ret;
#else
	return vfs_symlink(lnk, path);
#endif
}

/* NOFOLLOW_HIT_SYMLINK() lives in rsync.h (shared with util1.c's change_dir). */

#if defined NO_SYMLINK_XATTRS || defined NO_SYMLINK_USER_XATTRS
ssize_t vfs_readlink(const char *path, char *buf, size_t bufsiz)
{
	/* For --fake-super, we read the link from the file. */
	if (am_root < 0) {
		int fd = vfs_open_nofollow(path, O_RDONLY);
		if (fd >= 0) {
			int len = read(fd, buf, bufsiz);
			close(fd);
			return len;
		}
		if (!NOFOLLOW_HIT_SYMLINK(errno))
			return -1;
		/* A real symlink needs to be turned into a fake one on the receiving
		 * side, so tell the generator that the link has no length. */
		if (!am_sender)
			return 0;
		/* Otherwise fall through and let the sender report the real length. */
	}

	return readlink(path, buf, bufsiz);
}
#endif

ssize_t vfs_readlink_atfd(int dfd, const char *name, char *buf, size_t bufsiz)
{
#ifdef AT_FDCWD
# if defined NO_SYMLINK_XATTRS || defined NO_SYMLINK_USER_XATTRS
	if (am_root < 0) {
		int fd = openat(dfd, name, O_RDONLY | O_NOFOLLOW);
		if (fd >= 0) {
			int len = read(fd, buf, bufsiz);
			close(fd);
			return len;
		}
		if (!NOFOLLOW_HIT_SYMLINK(errno))
			return -1;
		if (!am_sender)
			return 0;
	}
# endif
	return readlinkat(dfd, name, buf, bufsiz);
#else
	(void)dfd;
	return vfs_readlink(name, buf, bufsiz);
#endif
}
#endif

static int vfs__symlink_atfd(const char *lnk, int dfd, const char *name)
{
#ifdef AT_FDCWD
	if (dry_run) return 0;
	RETURN_ERROR_IF_RO_OR_LO;

#if defined NO_SYMLINK_XATTRS || defined NO_SYMLINK_USER_XATTRS
	/* --fake-super: store the link target in a regular placeholder file,
	 * created with O_NOFOLLOW so a planted basename symlink can't redirect
	 * the write (mirrors vfs_symlink_at()). */
	if (am_root < 0) {
		int len = strlen(lnk);
		int ok;
		int fd = openat(dfd, name, O_WRONLY | O_CREAT | O_TRUNC | O_NOFOLLOW,
				S_IWUSR | S_IRUSR);
		if (fd < 0)
			return -1;
		ok = write(fd, lnk, len) == len;
		if (close(fd) < 0)
			ok = 0;
		return ok ? 0 : -1;
	}
#endif
	return symlinkat(lnk, dfd, name);
#else
	(void)lnk; (void)dfd; (void)name;
	errno = ENOSYS;
	return -1;
#endif
}

/* Unified symlink creation.  dirfd == VFS_AT_FDCWD resolves `path`; a real held
 * dirfd makes `path` a single validated component under it.  flags:
 * VFS_ALLOW_SYMLINK (trusted, plain symlink), default 0 (secure receiver
 * resolve).  See the note on vfs__symlink_secure re VFS_OPERATOR_PATH. */
int vfs_symlink(const char *lnk, int dirfd, const char *path, int flags)
{
	if (dirfd != VFS_AT_FDCWD) {
		if (!path || !*path || strchr(path, '/')
		 || (path[0] == '.' && (path[1] == '\0'
		     || (path[1] == '.' && path[2] == '\0')))) {
			errno = EINVAL;
			return -1;
		}
		return vfs__symlink_atfd(lnk, dirfd, path);
	}
	if (flags & VFS_ALLOW_SYMLINK)
		return vfs__symlink_plain(lnk, path);
	return vfs__symlink_secure(lnk, path, flags);
}

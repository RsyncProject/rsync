/*
 * vfs/mknod.c - device/fifo/socket node creation wrappers.
 *
 * Includes the HAVE_MKNOD/HAVE_MKNODAT/HAVE_MKFIFO and AF_UNIX socket-bind
 * fallbacks and the fake-super placeholder handling.  Moved verbatim out of
 * syscall.c.
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
#ifdef HAVE_SYS_UN_H
#include <sys/un.h>	/* for the socket+bind() fallback in vfs_mknod() */
#endif

int vfs_mknod(const char *pathname, mode_t mode, dev_t dev)
{
	if (dry_run) return 0;
	RETURN_ERROR_IF_RO_OR_LO;
	RETURN_ERROR_IF_NULL(pathname);

	/* For --fake-super, we create a normal file with mode 0600. */
	if (am_root < 0) {
		int fd = open(pathname, O_WRONLY|O_CREAT|O_TRUNC, S_IWUSR|S_IRUSR);
		if (fd < 0 || close(fd) < 0)
			return -1;
		return 0;
	}

	/* Try mknod first: it handles every node type on Linux.  Only if it
	 * can't make this type on this filesystem (sockets on the BSDs/macOS/
	 * Solaris, or an old system lacking FIFO support) do we retry with the
	 * type-specific primitive.  That capability is filesystem-dependent, so
	 * it is decided per call -- not cached, not probed at build time. */
#ifdef HAVE_MKNOD
	if (mknod(pathname, mode, dev) == 0)
		return 0;
#endif
#ifdef HAVE_MKFIFO
	if (S_ISFIFO(mode))
		return mkfifo(pathname, mode);
#endif
#ifdef HAVE_SYS_UN_H
	if (S_ISSOCK(mode)) {
		int sock;
		struct sockaddr_un saddr;
		unsigned int len = strlcpy(saddr.sun_path, pathname, sizeof saddr.sun_path);
		if (len >= sizeof saddr.sun_path) {
			errno = ENAMETOOLONG;
			return -1;
		}
#ifdef HAVE_SOCKADDR_UN_LEN
		saddr.sun_len = len + 1;
#endif
		saddr.sun_family = AF_UNIX;

		if ((sock = socket(PF_UNIX, SOCK_STREAM, 0)) < 0
		 || (unlink(pathname) < 0 && errno != ENOENT)
		 || (bind(sock, (struct sockaddr*)&saddr, sizeof saddr)) < 0)
			return -1;
		close(sock);
#ifdef HAVE_CHMOD
		return vfs_chmod(pathname, mode);
#else
		return 0;
#endif
	}
#endif
#ifdef HAVE_MKNOD
	return -1;	/* mknod() failed for a regular/device node; errno is set */
#else
	errno = ENOSYS;
	return -1;
#endif
}

/*
  Symlink-race-safe variant of vfs_mknod() for receiver-side use. See
  the comment on vfs_chmod_at() for the threat model. Defence: open
  the parent of pathname under vfs_resolve_open() and use
  mknodat() against that dirfd. mknodat() covers both regular-file
  (S_IFREG with dev=0) and FIFO (S_IFIFO) and device-node creation.

  A top-level (no-slash) pathname has no parent to confine, so it uses
  AT_FDCWD; the final component is still protected (mknodat/mkfifoat do
  not follow it, and the fake-super openat() uses O_NOFOLLOW).

  Fake-super (am_root < 0) is handled inline against the (secure or
  AT_FDCWD) dirfd: it creates a regular empty file (the same file-as-
  metadata-placeholder pattern vfs_mknod uses) via openat() with
  O_NOFOLLOW so a pre-planted symlink at the basename can't redirect
  the file creation -- top-level paths included (the previous code fell
  through to the bare-path vfs_mknod() there, whose plain open() followed
  such a symlink). On Linux, sockets are recreated with mknodat() like any
  other special file; on systems where mknod() can't create sockets the
  at-variant fails instead of re-resolving an unsafe parent.
*/
int vfs_mknod_at(const char *pathname, mode_t mode, dev_t dev)
{
	/* HAVE_MKNODAT: older Darwin declares AT_FDCWD but not mknodat(), so
	 * the at-variant won't build there; fall back to vfs_mknod() (#896). */
#if defined(AT_FDCWD) && defined(HAVE_MKNODAT)
	char dirpath[MAXPATHLEN];
	const char *bname;
	const char *slash;
	int dfd = AT_FDCWD, ret, e;
	BOOL owns = False;
	size_t dlen;

	if (dry_run) return 0;
	RETURN_ERROR_IF_RO_OR_LO;

#if defined O_NOFOLLOW && defined O_DIRECTORY
	if (vfs.operator_path_resolve) {
		if (vfs_symlink_optout_allowed())
			return vfs_mknod(pathname, mode, dev);
		dfd = vfs_owner_walk_parent(pathname, &bname, vfs.operator_path_resolve);
		if (dfd < 0)
			return -1;
		ret = mknodat(dfd, bname, mode, dev);
		e = errno;
		close(dfd);
		errno = e;
		return ret;
	}
#endif

	if (!vfs_relpath_active())
		return vfs_mknod(pathname, mode, dev);

	if (!pathname || !*pathname || *pathname == '/')
		return vfs_mknod(pathname, mode, dev);

	/* A path with a slash needs vfs_resolve_open to confine its
	 * parent resolution; a top-level path lives in CWD (AT_FDCWD) with
	 * no parent to subvert. The final component is protected below
	 * regardless. */
	slash = strrchr(pathname, '/');
	if (slash) {
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
		owns = True;
	} else {
		bname = pathname;
	}

	if (am_root < 0) {
		/* For --fake-super, vfs_mknod creates a regular empty
		 * file as a placeholder for the special-file metadata
		 * (which is stored in xattrs elsewhere). Do that against
		 * the (secure or AT_FDCWD) dirfd, with O_NOFOLLOW so a
		 * pre-planted symlink at the basename can't redirect the
		 * file creation. */
		int fd = openat(dfd, bname,
				O_WRONLY | O_CREAT | O_TRUNC | O_NOFOLLOW,
				S_IWUSR | S_IRUSR);
		if (fd < 0) {
			e = errno;
			if (owns) close(dfd);
			errno = e;
			return -1;
		}
		ret = (close(fd) < 0) ? -1 : 0;
		e = errno;
		if (owns) close(dfd);
		errno = e;
		return ret;
	}

	/* Try mknodat first (handles every type on Linux); on failure retry
	 * race-safely with the type-specific primitive.  Decided per call --
	 * the capability is filesystem-dependent (see vfs_mknod()). */
	ret = mknodat(dfd, bname, mode, dev);
	if (ret < 0) {
#ifdef HAVE_MKFIFOAT
		if (S_ISFIFO(mode))
			ret = mkfifoat(dfd, bname, mode);
		else
#endif
		if (S_ISSOCK(mode)) {
			/* There is no dirfd-relative socket bind without
			 * /proc/self/fd: a top-level path can bind via
			 * vfs_mknod(), but a nested one fails safe rather than
			 * re-resolve a potentially unsafe parent. */
			if (dfd == AT_FDCWD)
				ret = vfs_mknod(pathname, mode, dev);
			else
				errno = EOPNOTSUPP;
		}
		/* else: regular/device node -- keep mknodat()'s errno */
	}
	e = errno;
	if (owns) close(dfd);
	errno = e;
	return ret;
#else
	return vfs_mknod(pathname, mode, dev);
#endif
}

int vfs_mknod_atfd(int dfd, const char *name, mode_t mode, dev_t dev)
{
#ifdef AT_FDCWD
	if (dry_run) return 0;
	RETURN_ERROR_IF_RO_OR_LO;

	if (am_root < 0) {
		/* --fake-super: regular empty placeholder file (O_NOFOLLOW). */
		int fd = openat(dfd, name, O_WRONLY | O_CREAT | O_TRUNC | O_NOFOLLOW,
				S_IWUSR | S_IRUSR);
		if (fd < 0)
			return -1;
		return (close(fd) < 0) ? -1 : 0;
	}

	/* Try mknodat first; on failure retry race-safely with the type-
	 * specific primitive (see vfs_mknod()). */
#ifdef HAVE_MKNOD
	if (mknodat(dfd, name, mode, dev) == 0)
		return 0;
#endif
#ifdef HAVE_MKFIFOAT
	if (S_ISFIFO(mode))
		return mkfifoat(dfd, name, mode);
#endif
	if (S_ISSOCK(mode)) {
		/* No dirfd-relative socket bind without /proc/self/fd; fail safe.
		 * (The generator routes sockets to vfs_mknod_at(), not here.) */
		errno = EOPNOTSUPP;
		return -1;
	}
#ifdef HAVE_MKNOD
	return -1;	/* mknodat()'s errno (regular/device node) */
#else
	(void)dev;
	errno = ENOSYS;
	return -1;
#endif
#else
	(void)dfd; (void)name; (void)mode; (void)dev;
	errno = ENOSYS;
	return -1;
#endif
}

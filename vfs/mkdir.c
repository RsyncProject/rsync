/*
 * vfs/mkdir.c - mkdir and mkstemp wrappers, plus the trim_trailing_slashes
 * path helper and the race-safe vfs_secure_mkstemp / vfs_mkstemp_atfd create loop.
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

/* Fill buf with len random bytes.  Prefers /dev/urandom for cryptographic
 * quality; falls back to rand() if /dev/urandom cannot be opened or read
 * (e.g. inside a chroot or container without /dev populated). */
static void rand_bytes(unsigned char *buf, size_t len)
{
#ifndef O_CLOEXEC
#define O_CLOEXEC 0
#endif
	int fd = open("/dev/urandom", O_RDONLY | O_CLOEXEC);
	if (fd >= 0) {
		ssize_t n = read(fd, buf, len);
		close(fd);
		if (n == (ssize_t)len) {
			return;
		}
	}
	for (size_t i = 0; i < len; i++) {
		buf[i] = (unsigned char)rand();
	}
}

void trim_trailing_slashes(char *name)
{
	int l;
	/* Some BSD systems cannot make a directory if the name
	 * contains a trailing slash.
	 * <http://www.opensource.apple.com/bugs/X/BSD%20Kernel/2734739.html> */

	/* Don't change empty string; and also we can't improve on
	 * "/" */

	l = strlen(name);
	while (l > 1) {
		if (name[--l] != '/')
			break;
		name[l] = '\0';
	}
}

/* Secure receiver-side resolve for a path mkdir.  An operator path
 * (--backup-dir/--temp-dir, may live outside the tree) uses the ownership walk;
 * otherwise the strict transfer-path resolver (refuse all symlinks, confine
 * beneath the transfer root).  mkdir() resolves parent symlinks at every
 * component, so a parent-component swap can place an attacker-named directory
 * outside the module -- defence is to resolve the parent securely and mkdirat()
 * the leaf.  Falls through to a plain mkdir() in non-daemon/sender, chrooted,
 * no-parent and absolute-path cases. */
static int vfs__mkdir_secure(char *path, mode_t mode, int flags)
{
#if defined AT_FDCWD && defined O_NOFOLLOW && defined O_DIRECTORY
	char dirpath[MAXPATHLEN];
	const char *bname, *slash;
	int dfd, ret, e;
	size_t dlen;

	if (flags & VFS_OPERATOR_PATH) {
		if (vfs_symlink_optout_allowed())
			return mkdir(path, mode);
		dfd = vfs_owner_walk_parent(path, &bname, 1);
		if (dfd < 0)
			return -1;
		ret = mkdirat(dfd, bname, mode);
		e = errno;
		close(dfd);
		errno = e;
		return ret;
	}

	if (!vfs_relpath_active())
		return mkdir(path, mode);
	if (!*path || *path == '/')
		return mkdir(path, mode);
	slash = strrchr(path, '/');
	if (!slash)
		return mkdir(path, mode);
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
	ret = mkdirat(dfd, bname, mode);
	e = errno;
	close(dfd);
	errno = e;
	return ret;
#else
	(void)flags;
	return mkdir(path, mode);
#endif
}

/* Unified mkdir.  dirfd == VFS_AT_FDCWD resolves `path`; a real held dirfd makes
 * `path` a single component created directly under it.  flags: VFS_ALLOW_SYMLINK
 * (trusted, follow symlinks), VFS_OPERATOR_PATH (operator-supplied path),
 * default 0 (secure receiver resolve). */
int vfs_mkdir(int dirfd, char *path, mode_t mode, int flags)
{
	if (dry_run) return 0;
	RETURN_ERROR_IF_RO_OR_LO;
	RETURN_ERROR_IF_NULL(path);

	if (dirfd != VFS_AT_FDCWD) {
#ifdef AT_FDCWD
		/* Held-fd: `path` must be a single harmless component -- reject
		 * anything that could reintroduce path resolution under the pinned
		 * dir (empty, any '/', "." or ".."). */
		if (!*path || strchr(path, '/')
		 || (path[0] == '.' && (path[1] == '\0'
		     || (path[1] == '.' && path[2] == '\0')))) {
			errno = EINVAL;
			return -1;
		}
		return mkdirat(dirfd, path, mode);
#else
		(void)dirfd; (void)mode;
		errno = ENOSYS;
		return -1;
#endif
	}

	trim_trailing_slashes(path);

	/* VFS_ALLOW_SYMLINK takes precedence: the call site asserts the path is
	 * trusted, so follow symlinks (the legacy plain mkdir). */
	if (flags & VFS_ALLOW_SYMLINK)
		return mkdir(path, mode);

	return vfs__mkdir_secure(path, mode, flags);
}

/* like mkstemp but forces permissions */
int vfs_mkstemp(char *template, mode_t perms)
{
	RETURN_ERROR_IF(dry_run, 0);
	RETURN_ERROR_IF(read_only, EROFS);
	perms |= S_IWUSR;

#if defined HAVE_SECURE_MKSTEMP && defined HAVE_FCHMOD && (!defined HAVE_OPEN64 || defined HAVE_MKSTEMP64)
	{
		int fd = mkstemp(template);
		if (fd == -1)
			return -1;
		if (fchmod(fd, perms) != 0 && preserve_perms) {
			int errno_save = errno;
			close(fd);
			unlink(template);
			errno = errno_save;
			return -1;
		}
#if defined HAVE_SETMODE && O_BINARY
		setmode(fd, O_BINARY);
#endif
		return fd;
	}
#else
	if (!mktemp(template))
		return -1;
	return vfs_open(template, O_RDWR|O_EXCL|O_CREAT, perms);
#endif
}

/* Create a unique temp file directly in directory `dfd` for the held-dirfd
 * traversal: `filename` is the basename ending in "XXXXXX", rewritten in place
 * to the chosen name.  O_EXCL|O_NOFOLLOW so a planted name can't be followed or
 * clobbered.  Does NOT close dfd (the caller owns it).  Returns the fd, or -1.
 * This is the create loop shared with vfs_secure_mkstemp(). */
int vfs_mkstemp_atfd(int dfd, char *filename, mode_t perms)
{
#ifdef AT_FDCWD
	static const char letters[] = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789";
	size_t filename_len = strlen(filename);
	char *suffix;
	int fd = -1;

	if (filename_len < 6) {
		errno = EINVAL;
		return -1;
	}
	suffix = filename + filename_len - 6; /* Points to XXXXXX */
	if (strcmp(suffix, "XXXXXX") != 0) {
		errno = EINVAL;
		return -1;
	}

	perms |= S_IWUSR;
	for (int tries = 0; tries < 100; tries++) {
		unsigned char rbytes[6];
		rand_bytes(rbytes, sizeof(rbytes));
		for (int i = 0; i < 6; i++)
			suffix[i] = letters[rbytes[i] % (sizeof(letters) - 1)];

		fd = openat(dfd, filename, O_RDWR | O_CREAT | O_EXCL | O_NOFOLLOW, perms);
		if (fd >= 0)
			break;
		if (errno != EEXIST)
			return -1;
	}

	if (fd >= 0) {
		if (fchmod(fd, perms) != 0 && preserve_perms) {
			int errno_save = errno;
			close(fd);
			unlinkat(dfd, filename, 0);
			errno = errno_save;
			return -1;
		}
#if defined HAVE_SETMODE && O_BINARY
		setmode(fd, O_BINARY);
#endif
	}
	return fd;
#else
	(void)dfd; (void)filename; (void)perms;
	errno = ENOSYS;
	return -1;
#endif
}

/*
  Secure version of mkstemp that prevents symlink attacks on parent directories.
  Like vfs_resolve_open(), this walks the path checking each component
  with O_NOFOLLOW to prevent TOCTOU race conditions.

  The template may be relative or absolute, but must not contain ../ components.
  Returns fd on success, -1 on error.
*/
int vfs_secure_mkstemp(char *template, mode_t perms, int operator_path)
{
#if !defined(O_NOFOLLOW) || !defined(O_DIRECTORY) || !defined(AT_FDCWD)
	/* Fall back to regular mkstemp on old systems */
	return vfs_mkstemp(template, perms);
#else
	char *lastslash;
	int dirfd = AT_FDCWD;
	int fd = -1;

	if (!template) {
		errno = EINVAL;
		return -1;
	}
	if (strncmp(template, "../", 3) == 0 || strstr(template, "/../")) {
		errno = EINVAL;
		return -1;
	}

	/* An operator-supplied --temp-dir may point outside the tree; --insecure-links
	 * (or a daemon module's "insecure links =") restores legacy following. */
	if (operator_path && vfs_symlink_optout_allowed())
		return vfs_mkstemp(template, perms);

	/* Open the temp file's directory.  For an operator --temp-dir use the
	 * ownership walk (follow a uid0/euid-owned symlink, refuse a foreign one,
	 * absolute and relative alike); otherwise -- the deep-entry-dir fallback when
	 * the held-dirfd cache declines -- use the strict transfer-path resolver
	 * (refuse all symlinks, confine beneath the transfer root).  The temp file
	 * itself is created below with O_EXCL|O_NOFOLLOW, so a planted name can't be
	 * followed either way. */
	lastslash = strrchr(template, '/');
	if (lastslash) {
		char dirbuf[MAXPATHLEN];
		size_t dlen = lastslash - template;
		const char *dir;
		if (dlen == 0)
			dir = "/";
		else {
			if (dlen >= sizeof dirbuf) {
				errno = ENAMETOOLONG;
				return -1;
			}
			memcpy(dirbuf, template, dlen);
			dirbuf[dlen] = '\0';
			dir = dirbuf;
		}
		dirfd = operator_path
		      ? vfs_open_owner_walk(dir, O_RDONLY | O_DIRECTORY, 0, 0)
		      : vfs_resolve_open(dir, ".", O_RDONLY | O_DIRECTORY, 0);
		if (dirfd < 0)
			return -1;
	}

	/* Create the temp file in the securely-opened directory. */
	{
		char *filename = lastslash ? lastslash + 1 : template;
		int e;
		fd = vfs_mkstemp_atfd(dirfd, filename, perms);
		e = errno;
		if (dirfd != AT_FDCWD) close(dirfd);
		errno = e;
	}
	return fd;
#endif
}

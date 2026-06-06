/*
 * Syscall wrappers to ensure that nothing gets done in dry_run mode
 * and to handle system peculiarities.
 *
 * Copyright (C) 1998 Andrew Tridgell
 * Copyright (C) 2002 Martin Pool
 * Copyright (C) 2003-2022 Wayne Davison
 *
 * This program is free software; you can redistribute it and/or modify
 * it under the terms of the GNU General Public License as published by
 * the Free Software Foundation; either version 3 of the License, or
 * (at your option) any later version.
 *
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License along
 * with this program; if not, visit the http://fsf.org website.
 */

#include "rsync.h"

#if !defined MKNOD_CREATES_SOCKETS && defined HAVE_SYS_UN_H
#include <sys/un.h>
#endif
#ifdef HAVE_SYS_ATTR_H
#include <sys/attr.h>
#endif

#if defined HAVE_SYS_FALLOCATE && !defined HAVE_FALLOCATE
#include <sys/syscall.h>
#endif

#if defined(__linux__) && defined(HAVE_OPENAT2)
#include <sys/syscall.h>
#include <linux/openat2.h>
#endif

#include "ifuncs.h"

extern int dry_run;
extern int am_root;
extern int am_sender;
extern int read_only;
extern int list_only;
extern int inplace;
extern int preallocate_files;
extern int preserve_perms;
extern int preserve_executability;
extern int open_noatime;
extern int copy_links;
extern int copy_unsafe_links;

#ifndef S_BLKSIZE
# if defined hpux || defined __hpux__ || defined __hpux
#  define S_BLKSIZE 1024
# elif defined _AIX && defined _I386
#  define S_BLKSIZE 4096
# else
#  define S_BLKSIZE 512
# endif
#endif

#ifdef SUPPORT_CRTIMES
#ifdef HAVE_GETATTRLIST
#pragma pack(push, 4)
struct create_time {
	uint32 length;
	struct timespec crtime;
};
#pragma pack(pop)
#elif defined __CYGWIN__
#include <windows.h>
#endif
#endif

#define RETURN_ERROR_IF(x,e) \
	do { \
		if (x) { \
			errno = (e); \
			return -1; \
		} \
	} while (0)

#define RETURN_ERROR_IF_RO_OR_LO RETURN_ERROR_IF(read_only || list_only, EROFS)

int do_unlink(const char *path)
{
	if (dry_run) return 0;
	RETURN_ERROR_IF_RO_OR_LO;
	return unlink(path);
}

/*
  Symlink-race-safe variant of do_unlink() for receiver-side use. See
  the comment on do_chmod_at() for the threat model. unlink() resolves
  parent components, so a parent-symlink swap can delete an outside
  file under the daemon's authority. Defence: open the parent of path
  under secure_relative_open() and use unlinkat() (flags=0) against
  that dirfd.

  Falls through to do_unlink() for the same dry-run / non-daemon /
  chrooted / no-parent / absolute-path cases as the other wrappers.
*/
int do_unlink_at(const char *path)
{
#ifdef AT_FDCWD
	extern int am_daemon, am_chrooted;
	char dirpath[MAXPATHLEN];
	const char *bname;
	const char *slash;
	int dfd, ret, e;
	size_t dlen;

	if (dry_run) return 0;
	RETURN_ERROR_IF_RO_OR_LO;

	if (!am_daemon || am_chrooted)
		return unlink(path);

	if (!path || !*path || *path == '/')
		return unlink(path);

	slash = strrchr(path, '/');
	if (!slash)
		return unlink(path);

	dlen = slash - path;
	if (dlen >= sizeof dirpath) {
		errno = ENAMETOOLONG;
		return -1;
	}
	memcpy(dirpath, path, dlen);
	dirpath[dlen] = '\0';
	bname = slash + 1;

	dfd = secure_relative_open(NULL, dirpath, O_RDONLY | O_DIRECTORY, 0);
	if (dfd < 0)
		return -1;

	ret = unlinkat(dfd, bname, 0);
	e = errno;
	close(dfd);
	errno = e;
	return ret;
#else
	return do_unlink(path);
#endif
}

#ifdef SUPPORT_LINKS
int do_symlink(const char *lnk, const char *path)
{
	if (dry_run) return 0;
	RETURN_ERROR_IF_RO_OR_LO;

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
  Symlink-race-safe variant of do_symlink() for receiver-side use. See
  the comment on do_chmod_at() for the threat model. Only the parent
  directory of `path` needs protection -- symlinkat() does not resolve
  the final component (it creates it). Defence: open parent of `path`
  under secure_relative_open() and call symlinkat() against that
  dirfd. The link target string `lnk` is stored verbatim and not
  resolved at creation time, so it doesn't need scrutiny here.

  Falls through to do_symlink() for the --fake-super (am_root < 0)
  path -- that code path opens `path` with do_open() which has its
  own (separate) symlink-race exposure tracked elsewhere.
*/
int do_symlink_at(const char *lnk, const char *path)
{
#ifdef AT_FDCWD
	extern int am_daemon, am_chrooted;
	char dirpath[MAXPATHLEN];
	const char *bname;
	const char *slash;
	int dfd, ret, e;
	size_t dlen;

	if (dry_run) return 0;
	RETURN_ERROR_IF_RO_OR_LO;

	if (!am_daemon || am_chrooted)
		return do_symlink(lnk, path);

	if (!path || !*path || *path == '/')
		return do_symlink(lnk, path);

	slash = strrchr(path, '/');
	if (!slash)
		return do_symlink(lnk, path);

	dlen = slash - path;
	if (dlen >= sizeof dirpath) {
		errno = ENAMETOOLONG;
		return -1;
	}
	memcpy(dirpath, path, dlen);
	dirpath[dlen] = '\0';
	bname = slash + 1;

	dfd = secure_relative_open(NULL, dirpath, O_RDONLY | O_DIRECTORY, 0);
	if (dfd < 0)
		return -1;

#if defined NO_SYMLINK_XATTRS || defined NO_SYMLINK_USER_XATTRS
	/* For --fake-super, do_symlink writes the link target into a
	 * regular file rather than creating a real symlink. Do that
	 * here against the secure dirfd, with O_NOFOLLOW so a pre-
	 * planted symlink at the basename can't redirect the file
	 * creation. (Previously the fake-super branch fell through to
	 * the bare-path do_symlink at the top of the function.) */
	if (am_root < 0) {
		int len = strlen(lnk);
		int fd = openat(dfd, bname,
				O_WRONLY | O_CREAT | O_TRUNC | O_NOFOLLOW,
				S_IWUSR | S_IRUSR);
		if (fd < 0) {
			e = errno;
			close(dfd);
			errno = e;
			return -1;
		}
		ret = (write(fd, lnk, len) == len) ? 0 : -1;
		if (close(fd) < 0)
			ret = -1;
		e = errno;
		close(dfd);
		errno = e;
		return ret;
	}
#endif

	ret = symlinkat(lnk, dfd, bname);
	e = errno;
	close(dfd);
	errno = e;
	return ret;
#else
	return do_symlink(lnk, path);
#endif
}

#if defined NO_SYMLINK_XATTRS || defined NO_SYMLINK_USER_XATTRS
ssize_t do_readlink(const char *path, char *buf, size_t bufsiz)
{
	/* For --fake-super, we read the link from the file. */
	if (am_root < 0) {
		int fd = do_open_nofollow(path, O_RDONLY);
		if (fd >= 0) {
			int len = read(fd, buf, bufsiz);
			close(fd);
			return len;
		}
		if (errno != ELOOP)
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
#endif

#if defined HAVE_LINK || defined HAVE_LINKAT
int do_link(const char *old_path, const char *new_path)
{
	if (dry_run) return 0;
	RETURN_ERROR_IF_RO_OR_LO;
#ifdef HAVE_LINKAT
	return linkat(AT_FDCWD, old_path, AT_FDCWD, new_path, 0);
#else
	return link(old_path, new_path);
#endif
}

/*
  Symlink-race-safe variant of do_link() for receiver-side use. See
  the comment on do_chmod_at() for the threat model. link() resolves
  parent components of *both* old_path and new_path, so a parent-
  symlink swap on either side can plant the new hard link outside
  the module, or hard-link an outside file into the module (read
  disclosure).

  Defence: open each parent under secure_relative_open() and use
  linkat() between the two dirfds, reusing one when the parents
  match. flags=0 matches the existing do_link() (don't follow a
  symbolic-link old_path). Only available on systems with linkat();
  pre-AT_FDCWD systems fall through to do_link().
*/
int do_link_at(const char *old_path, const char *new_path)
{
#if defined AT_FDCWD && defined HAVE_LINKAT
	extern int am_daemon, am_chrooted;
	char old_dirpath[MAXPATHLEN], new_dirpath[MAXPATHLEN];
	const char *old_bname, *new_bname;
	const char *old_slash, *new_slash;
	int old_dfd = AT_FDCWD, new_dfd = AT_FDCWD;
	BOOL old_owns = False, new_owns = False;
	int ret, e;
	size_t old_dlen = 0, new_dlen = 0;

	if (dry_run) return 0;
	RETURN_ERROR_IF_RO_OR_LO;

	if (!am_daemon || am_chrooted)
		return do_link(old_path, new_path);

	if (!old_path || !*old_path || *old_path == '/'
	 || !new_path || !*new_path || *new_path == '/')
		return do_link(old_path, new_path);

	old_slash = strrchr(old_path, '/');
	new_slash = strrchr(new_path, '/');

	/* Resolve each path's parent dir independently. A path without a
	 * slash lives in CWD (AT_FDCWD), no parent open required. A path
	 * with a slash needs secure_relative_open to confine its parent
	 * resolution -- otherwise a parent symlink (e.g. --link-dest=cd
	 * where cd -> /outside) lets the kernel-level linkat(AT_FDCWD,
	 * "cd/target.txt", ...) escape the module. */
	if (old_slash) {
		old_dlen = old_slash - old_path;
		if (old_dlen >= sizeof old_dirpath) { errno = ENAMETOOLONG; return -1; }
		memcpy(old_dirpath, old_path, old_dlen);
		old_dirpath[old_dlen] = '\0';
		old_bname = old_slash + 1;
		old_dfd = secure_relative_open(NULL, old_dirpath, O_RDONLY | O_DIRECTORY, 0);
		if (old_dfd < 0)
			return -1;
		old_owns = True;
	} else {
		old_bname = old_path;
	}

	if (new_slash) {
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
			new_dfd = secure_relative_open(NULL, new_dirpath, O_RDONLY | O_DIRECTORY, 0);
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
	return do_link(old_path, new_path);
#endif
}
#endif

int do_lchown(const char *path, uid_t owner, gid_t group)
{
	if (dry_run) return 0;
	RETURN_ERROR_IF_RO_OR_LO;
#ifndef HAVE_LCHOWN
#define lchown chown
#endif
	return lchown(path, owner, group);
}

/*
  Symlink-race-safe variant of do_lchown() for receiver-side use. See the
  comment on do_chmod_at() for the threat model and design rationale.

  Resolves the parent directory under secure_relative_open() and invokes
  fchownat(..., AT_SYMLINK_NOFOLLOW) against that dirfd, so that an
  attacker who substitutes a symlink into one of the parent components
  cannot redirect the chown outside the receiver's confinement. The
  AT_SYMLINK_NOFOLLOW flag matches lchown()'s "do not follow a final-
  component symlink" semantics.

  Falls through to do_lchown() in the dry-run / non-daemon / chrooted /
  absolute-path / no-parent cases, identical to do_chmod_at().
*/
int do_lchown_at(const char *fname, uid_t owner, gid_t group)
{
#ifdef AT_FDCWD
	extern int am_daemon, am_chrooted;
	char dirpath[MAXPATHLEN];
	const char *bname;
	const char *slash;
	int dfd, ret, e;
	size_t dlen;

	if (dry_run) return 0;
	RETURN_ERROR_IF_RO_OR_LO;

	if (!am_daemon || am_chrooted)
		return do_lchown(fname, owner, group);

	if (!fname || !*fname || *fname == '/')
		return do_lchown(fname, owner, group);

	slash = strrchr(fname, '/');
	if (!slash)
		return do_lchown(fname, owner, group);

	dlen = slash - fname;
	if (dlen >= sizeof dirpath) {
		errno = ENAMETOOLONG;
		return -1;
	}
	memcpy(dirpath, fname, dlen);
	dirpath[dlen] = '\0';
	bname = slash + 1;

	dfd = secure_relative_open(NULL, dirpath, O_RDONLY | O_DIRECTORY, 0);
	if (dfd < 0)
		return -1;

	ret = fchownat(dfd, bname, owner, group, AT_SYMLINK_NOFOLLOW);
	e = errno;
	close(dfd);
	errno = e;
	return ret;
#else
	return do_lchown(fname, owner, group);
#endif
}

int do_mknod(const char *pathname, mode_t mode, dev_t dev)
{
	if (dry_run) return 0;
	RETURN_ERROR_IF_RO_OR_LO;

	/* For --fake-super, we create a normal file with mode 0600. */
	if (am_root < 0) {
		int fd = open(pathname, O_WRONLY|O_CREAT|O_TRUNC, S_IWUSR|S_IRUSR);
		if (fd < 0 || close(fd) < 0)
			return -1;
		return 0;
	}

#if !defined MKNOD_CREATES_FIFOS && defined HAVE_MKFIFO
	if (S_ISFIFO(mode))
		return mkfifo(pathname, mode);
#endif
#if !defined MKNOD_CREATES_SOCKETS && defined HAVE_SYS_UN_H
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
		return do_chmod(pathname, mode);
#else
		return 0;
#endif
	}
#endif
#ifdef HAVE_MKNOD
	return mknod(pathname, mode, dev);
#else
	return -1;
#endif
}

/*
  Symlink-race-safe variant of do_mknod() for receiver-side use. See
  the comment on do_chmod_at() for the threat model. Defence: open
  the parent of pathname under secure_relative_open() and use
  mknodat() against that dirfd. mknodat() covers both regular-file
  (S_IFREG with dev=0) and FIFO (S_IFIFO) and device-node creation.

  Fake-super (am_root < 0) is handled inline against the secure
  parent dirfd: it creates a regular empty file (the same file-as-
  metadata-placeholder pattern do_mknod uses) via openat() with
  O_NOFOLLOW. Sockets fall through to do_mknod() because their
  bind(2) takes a path argument with no portable bindat() variant;
  this is documented as a residual.
*/
int do_mknod_at(const char *pathname, mode_t mode, dev_t dev)
{
	/* HAVE_MKNODAT: older Darwin declares AT_FDCWD but not mknodat(), so
	 * the at-variant won't build there; fall back to do_mknod() (#896). */
#if defined(AT_FDCWD) && defined(HAVE_MKNODAT)
	extern int am_daemon, am_chrooted;
	char dirpath[MAXPATHLEN];
	const char *bname;
	const char *slash;
	int dfd, ret, e;
	size_t dlen;

	if (dry_run) return 0;
	RETURN_ERROR_IF_RO_OR_LO;

	if (!am_daemon || am_chrooted)
		return do_mknod(pathname, mode, dev);

#if !defined MKNOD_CREATES_SOCKETS && defined HAVE_SYS_UN_H
	if (S_ISSOCK(mode))
		return do_mknod(pathname, mode, dev);
#endif

	if (!pathname || !*pathname || *pathname == '/')
		return do_mknod(pathname, mode, dev);

	slash = strrchr(pathname, '/');
	if (!slash)
		return do_mknod(pathname, mode, dev);

	dlen = slash - pathname;
	if (dlen >= sizeof dirpath) {
		errno = ENAMETOOLONG;
		return -1;
	}
	memcpy(dirpath, pathname, dlen);
	dirpath[dlen] = '\0';
	bname = slash + 1;

	dfd = secure_relative_open(NULL, dirpath, O_RDONLY | O_DIRECTORY, 0);
	if (dfd < 0)
		return -1;

	if (am_root < 0) {
		/* For --fake-super, do_mknod creates a regular empty
		 * file as a placeholder for the special-file metadata
		 * (which is stored in xattrs elsewhere). Do that against
		 * the secure dirfd, with O_NOFOLLOW so a pre-planted
		 * symlink at the basename can't redirect the file
		 * creation. */
		int fd = openat(dfd, bname,
				O_WRONLY | O_CREAT | O_TRUNC | O_NOFOLLOW,
				S_IWUSR | S_IRUSR);
		if (fd < 0) {
			e = errno;
			close(dfd);
			errno = e;
			return -1;
		}
		ret = (close(fd) < 0) ? -1 : 0;
		e = errno;
		close(dfd);
		errno = e;
		return ret;
	}

#if !defined MKNOD_CREATES_FIFOS && defined HAVE_MKFIFO && defined HAVE_MKFIFOAT
	if (S_ISFIFO(mode))
		ret = mkfifoat(dfd, bname, mode);
	else
#endif
		ret = mknodat(dfd, bname, mode, dev);
	e = errno;
	close(dfd);
	errno = e;
	return ret;
#else
	return do_mknod(pathname, mode, dev);
#endif
}

int do_rmdir(const char *pathname)
{
	if (dry_run) return 0;
	RETURN_ERROR_IF_RO_OR_LO;
	return rmdir(pathname);
}

/*
  Symlink-race-safe variant of do_rmdir(). See do_unlink_at() above;
  same shape but with AT_REMOVEDIR set to require the target be a
  directory.
*/
int do_rmdir_at(const char *pathname)
{
#ifdef AT_FDCWD
	extern int am_daemon, am_chrooted;
	char dirpath[MAXPATHLEN];
	const char *bname;
	const char *slash;
	int dfd, ret, e;
	size_t dlen;

	if (dry_run) return 0;
	RETURN_ERROR_IF_RO_OR_LO;

	if (!am_daemon || am_chrooted)
		return rmdir(pathname);

	if (!pathname || !*pathname || *pathname == '/')
		return rmdir(pathname);

	slash = strrchr(pathname, '/');
	if (!slash)
		return rmdir(pathname);

	dlen = slash - pathname;
	if (dlen >= sizeof dirpath) {
		errno = ENAMETOOLONG;
		return -1;
	}
	memcpy(dirpath, pathname, dlen);
	dirpath[dlen] = '\0';
	bname = slash + 1;

	dfd = secure_relative_open(NULL, dirpath, O_RDONLY | O_DIRECTORY, 0);
	if (dfd < 0)
		return -1;

	ret = unlinkat(dfd, bname, AT_REMOVEDIR);
	e = errno;
	close(dfd);
	errno = e;
	return ret;
#else
	return do_rmdir(pathname);
#endif
}

int do_open(const char *pathname, int flags, mode_t mode)
{
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
  Symlink-race-safe variant of do_open() for receiver-side use. See
  the comment on do_chmod_at() for the threat model. open() resolves
  parent components, so a parent-symlink swap can redirect the open
  to a file outside the module. This wrapper is defence-in-depth for
  bare-path do_open() sites that callers know are otherwise
  protected by secure parent-syscalls (e.g. generator.c's in-place
  backup creation, where robust_unlink() rejects the symlinked
  parent before this open is reached): if any of those upstream
  protections is later removed or regresses, the open here still
  refuses to escape the module.

  Defence: open the parent of pathname under secure_relative_open()
  and call openat() against the resulting dirfd with O_NOFOLLOW
  (so the basename itself isn't followed if it happens to be a
  pre-planted symlink, which is what we want for O_CREAT|O_EXCL).
*/
int do_open_at(const char *pathname, int flags, mode_t mode)
{
#ifdef AT_FDCWD
	extern int am_daemon, am_chrooted;
	char dirpath[MAXPATHLEN];
	const char *bname;
	const char *slash;
	int dfd, ret, e;
	size_t dlen;

	if (flags != O_RDONLY) {
		RETURN_ERROR_IF(dry_run, 0);
		RETURN_ERROR_IF_RO_OR_LO;
	}

	if (!am_daemon || am_chrooted)
		return do_open(pathname, flags, mode);

	if (!pathname || !*pathname || *pathname == '/')
		return do_open(pathname, flags, mode);

	slash = strrchr(pathname, '/');
	if (!slash)
		return do_open(pathname, flags, mode);

	dlen = slash - pathname;
	if (dlen >= sizeof dirpath) {
		errno = ENAMETOOLONG;
		return -1;
	}
	memcpy(dirpath, pathname, dlen);
	dirpath[dlen] = '\0';
	bname = slash + 1;

	dfd = secure_relative_open(NULL, dirpath, O_RDONLY | O_DIRECTORY, 0);
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
	return do_open(pathname, flags, mode);
#endif
}

#ifdef HAVE_CHMOD
int do_chmod(const char *path, mode_t mode)
{
	static int switch_step = 0;
	int code;

	if (dry_run) return 0;
	RETURN_ERROR_IF_RO_OR_LO;

	switch (switch_step) {
#ifdef HAVE_LCHMOD
	case 0:
		if ((code = lchmod(path, mode & CHMOD_BITS)) == 0)
			break;
		if (errno == ENOSYS)
			switch_step++;
		else if (errno != ENOTSUP)
			break;
#endif
		/* FALLTHROUGH */
	default:
		if (S_ISLNK(mode)) {
# if defined HAVE_SETATTRLIST
			struct attrlist attrList;
			uint32_t m = mode & CHMOD_BITS; /* manpage is wrong: not mode_t! */

			memset(&attrList, 0, sizeof attrList);
			attrList.bitmapcount = ATTR_BIT_MAP_COUNT;
			attrList.commonattr = ATTR_CMN_ACCESSMASK;
			if ((code = setattrlist(path, &attrList, &m, sizeof m, FSOPT_NOFOLLOW)) == 0)
				break;
			if (errno == ENOTSUP)
				code = 1;
# else
			code = 1;
# endif
		} else
			code = chmod(path, mode & CHMOD_BITS); /* DISCOURAGED FUNCTION */
		break;
	}
	if (code != 0 && (preserve_perms || preserve_executability))
		return code;
	return 0;
}

/*
  Symlink-race-safe variant of do_chmod() for receiver-side use.

  Threat model: on a daemon running with "use chroot = no" (the prerequisite
  for CVE-2026-29518), a local attacker can race a symlink swap of one of
  the parent directory components of a path the receiver is about to chmod.
  Because chmod() resolves symlinks at every component, the swap redirects
  the chmod outside the receiver's confinement.

  Defence: open the *parent* directory of fname under secure_relative_open()
  (which uses openat2(RESOLVE_BENEATH) on Linux 5.6+, openat() with
  O_RESOLVE_BENEATH on FreeBSD 13+ and macOS 15+ (Sequoia), or a per-component
  O_NOFOLLOW walk elsewhere) and do fchmodat() against that dirfd. A symlink
  substituted into one of the parent components is then either followed
  within the tree (legitimate dir-symlinks still work) or rejected by the
  kernel (escape attempts fail).

  Final-component handling matches do_chmod(): fchmodat() with flag 0
  follows a symlink at the final component, which is the same behaviour as
  chmod() and matches every current call site (the file being chmod'd is
  one the receiver itself just created or transferred). For the rare case
  where the caller wants to chmod a symlink-as-an-object (S_ISLNK in the
  mode bits), we fall through to do_chmod() which has portability code for
  that case.

  Falls back to do_chmod() for absolute paths and for paths with no parent
  component, where there is nothing to protect against.
*/
int do_chmod_at(const char *fname, mode_t mode)
{
#ifdef AT_FDCWD
	extern int am_daemon, am_chrooted;
	char dirpath[MAXPATHLEN];
	const char *bname;
	const char *slash;
	int dfd, ret, e;
	size_t dlen;

	if (dry_run) return 0;
	RETURN_ERROR_IF_RO_OR_LO;

	/* Only the daemon-without-chroot case is exposed to the symlink-
	 * race attack: a chroot already confines the receiver, and a
	 * non-daemon rsync runs with the user's own authority so a
	 * symlink they planted can only redirect to files they could
	 * already access.  Everywhere else, fall through to plain
	 * do_chmod() to avoid the dirfd-open overhead on every call. */
	if (!am_daemon || am_chrooted)
		return do_chmod(fname, mode);

	if (!fname || !*fname || *fname == '/' || S_ISLNK(mode))
		return do_chmod(fname, mode);

	slash = strrchr(fname, '/');
	if (!slash)
		return do_chmod(fname, mode);

	dlen = slash - fname;
	if (dlen >= sizeof dirpath) {
		errno = ENAMETOOLONG;
		return -1;
	}
	memcpy(dirpath, fname, dlen);
	dirpath[dlen] = '\0';
	bname = slash + 1;

	dfd = secure_relative_open(NULL, dirpath, O_RDONLY | O_DIRECTORY, 0);
	if (dfd < 0)
		return -1;

	ret = fchmodat(dfd, bname, mode, 0);
	e = errno;
	close(dfd);
	errno = e;
	return ret;
#else
	return do_chmod(fname, mode);
#endif
}
#endif

int do_rename(const char *old_path, const char *new_path)
{
	if (dry_run) return 0;
	RETURN_ERROR_IF_RO_OR_LO;
	return rename(old_path, new_path);
}

/*
  Symlink-race-safe variant of do_rename() for receiver-side use. See
  the comment on do_chmod_at() for the threat model and design rationale.

  rename() is the central tmp -> final operation in rsync; if either the
  source or the destination has an attacker-substituted symlink in one
  of its parent components, the rename can publish or vanish files
  outside the module. Defence: open the parent of *each* path under
  secure_relative_open() and use renameat() against the resulting
  dirfds. When old_path and new_path share the same parent (the common
  case -- tmp file living next to its final name), we reuse the same
  dirfd for both sides.

  Falls through to do_rename() in dry-run, non-daemon, chrooted, no-
  parent and absolute-path cases, identical to the other do_*_at()
  wrappers.
*/
int do_rename_at(const char *old_path, const char *new_path)
{
#ifdef AT_FDCWD
	extern int am_daemon, am_chrooted;
	char old_dirpath[MAXPATHLEN], new_dirpath[MAXPATHLEN];
	const char *old_bname, *new_bname;
	const char *old_slash, *new_slash;
	int old_dfd = -1, new_dfd = -1, ret = -1, e;
	size_t old_dlen, new_dlen;

	if (dry_run) return 0;
	RETURN_ERROR_IF_RO_OR_LO;

	if (!am_daemon || am_chrooted)
		return do_rename(old_path, new_path);

	if (!old_path || !*old_path || *old_path == '/'
	 || !new_path || !*new_path || *new_path == '/')
		return do_rename(old_path, new_path);

	old_slash = strrchr(old_path, '/');
	new_slash = strrchr(new_path, '/');
	if (!old_slash || !new_slash)
		return do_rename(old_path, new_path);

	old_dlen = old_slash - old_path;
	new_dlen = new_slash - new_path;
	if (old_dlen >= sizeof old_dirpath || new_dlen >= sizeof new_dirpath) {
		errno = ENAMETOOLONG;
		return -1;
	}
	memcpy(old_dirpath, old_path, old_dlen);
	old_dirpath[old_dlen] = '\0';
	memcpy(new_dirpath, new_path, new_dlen);
	new_dirpath[new_dlen] = '\0';
	old_bname = old_slash + 1;
	new_bname = new_slash + 1;

	old_dfd = secure_relative_open(NULL, old_dirpath, O_RDONLY | O_DIRECTORY, 0);
	if (old_dfd < 0)
		return -1;

	if (old_dlen == new_dlen && memcmp(old_dirpath, new_dirpath, old_dlen) == 0) {
		new_dfd = old_dfd;
	} else {
		new_dfd = secure_relative_open(NULL, new_dirpath, O_RDONLY | O_DIRECTORY, 0);
		if (new_dfd < 0) {
			e = errno;
			close(old_dfd);
			errno = e;
			return -1;
		}
	}

	ret = renameat(old_dfd, old_bname, new_dfd, new_bname);
	e = errno;
	if (new_dfd != old_dfd)
		close(new_dfd);
	close(old_dfd);
	errno = e;
	return ret;
#else
	return do_rename(old_path, new_path);
#endif
}

#ifdef HAVE_FTRUNCATE
int do_ftruncate(int fd, OFF_T size)
{
	int ret;

	if (dry_run) return 0;
	RETURN_ERROR_IF_RO_OR_LO;

	do {
		ret = ftruncate(fd, size);
	} while (ret < 0 && errno == EINTR);

	return ret;
}
#endif

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

int do_mkdir(char *path, mode_t mode)
{
	if (dry_run) return 0;
	RETURN_ERROR_IF_RO_OR_LO;
	trim_trailing_slashes(path);
	return mkdir(path, mode);
}

/*
  Symlink-race-safe variant of do_mkdir() for receiver-side use. See
  the comment on do_chmod_at() for the threat model and design rationale.

  mkdir() resolves parent symlinks at every component, so a parent-
  component swap can place an attacker-named directory outside the
  module. Defence: open the parent of fname under secure_relative_open()
  and call mkdirat() against that dirfd.

  Mutates path in place to trim trailing slashes (matches do_mkdir()).
  Falls through to do_mkdir() in dry-run, non-daemon, chrooted, no-
  parent and absolute-path cases.
*/
int do_mkdir_at(char *path, mode_t mode)
{
#ifdef AT_FDCWD
	extern int am_daemon, am_chrooted;
	char dirpath[MAXPATHLEN];
	const char *bname;
	const char *slash;
	int dfd, ret, e;
	size_t dlen;

	if (dry_run) return 0;
	RETURN_ERROR_IF_RO_OR_LO;
	trim_trailing_slashes(path);

	if (!am_daemon || am_chrooted)
		return mkdir(path, mode);

	if (!path || !*path || *path == '/')
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

	dfd = secure_relative_open(NULL, dirpath, O_RDONLY | O_DIRECTORY, 0);
	if (dfd < 0)
		return -1;

	ret = mkdirat(dfd, bname, mode);
	e = errno;
	close(dfd);
	errno = e;
	return ret;
#else
	return do_mkdir(path, mode);
#endif
}

/* like mkstemp but forces permissions */
int do_mkstemp(char *template, mode_t perms)
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
	return do_open(template, O_RDWR|O_EXCL|O_CREAT, perms);
#endif
}

int do_stat(const char *path, STRUCT_STAT *st)
{
#ifdef USE_STAT64_FUNCS
	return stat64(path, st);
#else
	return stat(path, st);
#endif
}

int do_lstat(const char *path, STRUCT_STAT *st)
{
#ifdef SUPPORT_LINKS
# ifdef USE_STAT64_FUNCS
	return lstat64(path, st);
# else
	return lstat(path, st);
# endif
#else
	return do_stat(path, st);
#endif
}

/*
  Symlink-race-safe variants of do_stat() / do_lstat() for receiver-
  side use. See the comment on do_chmod_at() for the threat model.
  stat() and lstat() resolve parent components, so a parent-symlink
  swap can make the receiver's stat see attributes of a victim file
  outside the module -- which then drives later behaviour (e.g.
  "this isn't a directory, delete it" -> attacker-controlled unlink
  on something outside the module).

  Defence: open the parent under secure_relative_open() and use
  fstatat() with AT_SYMLINK_NOFOLLOW (lstat) or 0 (stat) against
  that dirfd. Same fall-through gating as the other wrappers.
*/
static int do_xstat_at(const char *path, STRUCT_STAT *st, int at_flags, int (*fallback)(const char *, STRUCT_STAT *))
{
#ifdef AT_FDCWD
	extern int am_daemon, am_chrooted;
	char dirpath[MAXPATHLEN];
	const char *bname;
	const char *slash;
	int dfd, ret, e;
	size_t dlen;

	if (!am_daemon || am_chrooted)
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

	dfd = secure_relative_open(NULL, dirpath, O_RDONLY | O_DIRECTORY, 0);
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

int do_stat_at(const char *path, STRUCT_STAT *st)
{
	return do_xstat_at(path, st, 0, do_stat);
}

int do_lstat_at(const char *path, STRUCT_STAT *st)
{
#ifdef SUPPORT_LINKS
	return do_xstat_at(path, st, AT_SYMLINK_NOFOLLOW, do_lstat);
#else
	return do_xstat_at(path, st, 0, do_stat);
#endif
}

int do_fstat(int fd, STRUCT_STAT *st)
{
#ifdef USE_STAT64_FUNCS
	return fstat64(fd, st);
#else
	return fstat(fd, st);
#endif
}

OFF_T do_lseek(int fd, OFF_T offset, int whence)
{
#ifdef HAVE_LSEEK64
	return lseek64(fd, offset, whence);
#else
	return lseek(fd, offset, whence);
#endif
}

#ifdef HAVE_SETATTRLIST
int do_setattrlist_times(const char *path, STRUCT_STAT *stp)
{
	extern int am_daemon, am_chrooted;
	struct attrlist attrList;
	struct timespec ts[2];

	if (dry_run) return 0;
	RETURN_ERROR_IF_RO_OR_LO;

	/* setattrlist() takes a raw path and follows parent symlinks
	 * (FSOPT_NOFOLLOW only blocks the final component). On a
	 * daemon-no-chroot deployment, return ENOSYS so set_times()'
	 * tier walk falls through to do_utimensat_at(), which routes
	 * the timestamp update through a secure parent dirfd. The
	 * macOS-specific attribute set this function would have used
	 * (ATTR_CMN_MODTIME / ATTR_CMN_ACCTIME) is the same set
	 * utimensat() handles, so no functionality is lost. */
	if (am_daemon && !am_chrooted) {
		errno = ENOSYS;
		return -1;
	}

	/* Yes, this is in the opposite order of utime and similar. */
	ts[0].tv_sec = stp->st_mtime;
	ts[0].tv_nsec = stp->ST_MTIME_NSEC;

	ts[1].tv_sec = stp->st_atime;
	ts[1].tv_nsec = stp->ST_ATIME_NSEC;

	memset(&attrList, 0, sizeof attrList);
	attrList.bitmapcount = ATTR_BIT_MAP_COUNT;
	attrList.commonattr = ATTR_CMN_MODTIME | ATTR_CMN_ACCTIME;
	return setattrlist(path, &attrList, ts, sizeof ts, FSOPT_NOFOLLOW);
}

#ifdef SUPPORT_CRTIMES
int do_setattrlist_crtime(const char *path, time_t crtime)
{
	extern int am_daemon, am_chrooted;
	struct attrlist attrList;
	struct timespec ts;

	if (dry_run) return 0;
	RETURN_ERROR_IF_RO_OR_LO;

	/* Same path-follows-parent-symlinks concern as
	 * do_setattrlist_times. There is no portable at-aware variant
	 * of setattrlist that targets ATTR_CMN_CRTIME, so on a
	 * daemon-no-chroot deployment we return -1 and accept that
	 * crtime preservation is silently dropped for that file (the
	 * caller treats this as "crtime not updated"). The transfer
	 * itself continues normally. */
	if (am_daemon && !am_chrooted) {
		errno = ENOSYS;
		return -1;
	}

	ts.tv_sec = crtime;
	ts.tv_nsec = 0;

	memset(&attrList, 0, sizeof attrList);
	attrList.bitmapcount = ATTR_BIT_MAP_COUNT;
	attrList.commonattr = ATTR_CMN_CRTIME;
	return setattrlist(path, &attrList, &ts, sizeof ts, FSOPT_NOFOLLOW);
}
#endif
#endif /* HAVE_SETATTRLIST */

#ifdef SUPPORT_CRTIMES
time_t get_create_time(const char *path, STRUCT_STAT *stp)
{
#ifdef HAVE_GETATTRLIST
	extern int am_daemon, am_chrooted;
	static struct create_time attrBuf;
	struct attrlist attrList;

	(void)stp;
	/* getattrlist() is also path-based and follows parent
	 * symlinks. In daemon-no-chroot, refuse rather than read the
	 * crtime of a file the parent-symlink chain might point at
	 * outside the module. The caller's "no crtime available"
	 * path returns 0; the file gets a fresh crtime instead of
	 * preserving the source's. */
	if (am_daemon && !am_chrooted)
		return 0;
	memset(&attrList, 0, sizeof attrList);
	attrList.bitmapcount = ATTR_BIT_MAP_COUNT;
	attrList.commonattr = ATTR_CMN_CRTIME;
	if (getattrlist(path, &attrList, &attrBuf, sizeof attrBuf, FSOPT_NOFOLLOW) < 0)
		return 0;
	return attrBuf.crtime.tv_sec;
#elif defined __CYGWIN__
	(void)path;
	return stp->st_birthtime;
#else
#error Unknown crtimes implementation
#endif
}

#if defined __CYGWIN__
int do_SetFileTime(const char *path, time_t crtime)
{
	if (dry_run) return 0;
	RETURN_ERROR_IF_RO_OR_LO;

	int cnt = MultiByteToWideChar(CP_UTF8, 0, path, -1, NULL, 0);
	if (cnt == 0)
	    return -1;
	WCHAR *pathw = new_array(WCHAR, cnt);
	if (!pathw)
	    return -1;
	MultiByteToWideChar(CP_UTF8, 0, path, -1, pathw, cnt);
	HANDLE handle = CreateFileW(pathw, FILE_WRITE_ATTRIBUTES, FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
				    NULL, OPEN_EXISTING, FILE_FLAG_BACKUP_SEMANTICS, NULL);
	free(pathw);
	if (handle == INVALID_HANDLE_VALUE)
	    return -1;
	int64 temp_time = (crtime * 10000000LL) + 116444736000000000LL;
	FILETIME birth_time;
	birth_time.dwLowDateTime = (DWORD)temp_time;
	birth_time.dwHighDateTime = (DWORD)(temp_time >> 32);
	int ok = SetFileTime(handle, &birth_time, NULL, NULL);
	CloseHandle(handle);
	return ok ? 0 : -1;
}
#endif
#endif /* SUPPORT_CRTIMES */

#ifdef HAVE_UTIMENSAT
int do_utimensat(const char *path, STRUCT_STAT *stp)
{
	struct timespec t[2];

	if (dry_run) return 0;
	RETURN_ERROR_IF_RO_OR_LO;

	t[0].tv_sec = stp->st_atime;
#ifdef ST_ATIME_NSEC
	t[0].tv_nsec = stp->ST_ATIME_NSEC;
#else
	t[0].tv_nsec = 0;
#endif
	t[1].tv_sec = stp->st_mtime;
#ifdef ST_MTIME_NSEC
	t[1].tv_nsec = stp->ST_MTIME_NSEC;
#else
	t[1].tv_nsec = 0;
#endif
	return utimensat(AT_FDCWD, path, t, AT_SYMLINK_NOFOLLOW);
}

/*
  Symlink-race-safe variant of do_utimensat() for receiver-side use.
  See the comment on do_chmod_at() for the threat model. utimes()
  resolves parent components and follows a final-component symlink;
  lutimes() doesn't follow the final component but still resolves
  parents. Either way, a parent-symlink swap can redirect the
  timestamp update outside the module. Defence: open the parent of
  path under secure_relative_open() and call utimensat() with
  AT_SYMLINK_NOFOLLOW against that dirfd.

  Falls through to do_utimensat() in the same dry-run / non-daemon /
  chrooted / no-parent / absolute-path cases as the other wrappers.
  Returns -1 with errno=ENOSYS on systems without utimensat()
  (caller is expected to fall back to the legacy tier walk).
*/
int do_utimensat_at(const char *path, STRUCT_STAT *stp)
{
#ifdef AT_FDCWD
	extern int am_daemon, am_chrooted;
	struct timespec t[2];
	char dirpath[MAXPATHLEN];
	const char *bname;
	const char *slash;
	int dfd, ret, e;
	size_t dlen;

	if (dry_run) return 0;
	RETURN_ERROR_IF_RO_OR_LO;

	if (!am_daemon || am_chrooted)
		return do_utimensat(path, stp);

	if (!path || !*path || *path == '/')
		return do_utimensat(path, stp);

	slash = strrchr(path, '/');
	if (!slash)
		return do_utimensat(path, stp);

	dlen = slash - path;
	if (dlen >= sizeof dirpath) {
		errno = ENAMETOOLONG;
		return -1;
	}
	memcpy(dirpath, path, dlen);
	dirpath[dlen] = '\0';
	bname = slash + 1;

	t[0].tv_sec = stp->st_atime;
#ifdef ST_ATIME_NSEC
	t[0].tv_nsec = stp->ST_ATIME_NSEC;
#else
	t[0].tv_nsec = 0;
#endif
	t[1].tv_sec = stp->st_mtime;
#ifdef ST_MTIME_NSEC
	t[1].tv_nsec = stp->ST_MTIME_NSEC;
#else
	t[1].tv_nsec = 0;
#endif

	dfd = secure_relative_open(NULL, dirpath, O_RDONLY | O_DIRECTORY, 0);
	if (dfd < 0)
		return -1;

	ret = utimensat(dfd, bname, t, AT_SYMLINK_NOFOLLOW);
	e = errno;
	close(dfd);
	errno = e;
	return ret;
#else
	return do_utimensat(path, stp);
#endif
}
#endif

#ifdef HAVE_LUTIMES
int do_lutimes(const char *path, STRUCT_STAT *stp)
{
	struct timeval t[2];

	if (dry_run) return 0;
	RETURN_ERROR_IF_RO_OR_LO;

	t[0].tv_sec = stp->st_atime;
#ifdef ST_ATIME_NSEC
	t[0].tv_usec = stp->ST_ATIME_NSEC / 1000;
#else
	t[0].tv_usec = 0;
#endif
	t[1].tv_sec = stp->st_mtime;
#ifdef ST_MTIME_NSEC
	t[1].tv_usec = stp->ST_MTIME_NSEC / 1000;
#else
	t[1].tv_usec = 0;
#endif
	return lutimes(path, t);
}
#endif

#ifdef HAVE_UTIMES
int do_utimes(const char *path, STRUCT_STAT *stp)
{
	struct timeval t[2];

	if (dry_run) return 0;
	RETURN_ERROR_IF_RO_OR_LO;

	t[0].tv_sec = stp->st_atime;
#ifdef ST_ATIME_NSEC
	t[0].tv_usec = stp->ST_ATIME_NSEC / 1000;
#else
	t[0].tv_usec = 0;
#endif
	t[1].tv_sec = stp->st_mtime;
#ifdef ST_MTIME_NSEC
	t[1].tv_usec = stp->ST_MTIME_NSEC / 1000;
#else
	t[1].tv_usec = 0;
#endif
	return utimes(path, t);
}

#elif defined HAVE_UTIME
int do_utime(const char *path, STRUCT_STAT *stp)
{
#ifdef HAVE_STRUCT_UTIMBUF
	struct utimbuf tbuf;
#else
	time_t t[2];
#endif

	if (dry_run) return 0;
	RETURN_ERROR_IF_RO_OR_LO;

# ifdef HAVE_STRUCT_UTIMBUF
	tbuf.actime = stp->st_atime;
	tbuf.modtime = stp->st_mtime;
	return utime(path, &tbuf);
# else
	t[0] = stp->st_atime;
	t[1] = stp->st_mtime;
	return utime(path, t);
# endif
}

#else
#error Need utimes or utime function.
#endif

#ifdef SUPPORT_PREALLOCATION
#ifdef FALLOC_FL_KEEP_SIZE
#define DO_FALLOC_OPTIONS FALLOC_FL_KEEP_SIZE
#else
#define DO_FALLOC_OPTIONS 0
#endif

OFF_T do_fallocate(int fd, OFF_T offset, OFF_T length)
{
	int opts = inplace || preallocate_files ? DO_FALLOC_OPTIONS : 0;
	int ret;
	RETURN_ERROR_IF(dry_run, 0);
	RETURN_ERROR_IF_RO_OR_LO;
	if (length & 1) /* make the length not match the desired length */
		length++;
	else
		length--;
#if defined HAVE_FALLOCATE
	ret = fallocate(fd, opts, offset, length);
#elif defined HAVE_SYS_FALLOCATE
	ret = syscall(SYS_fallocate, fd, opts, (loff_t)offset, (loff_t)length);
#elif defined HAVE_EFFICIENT_POSIX_FALLOCATE
	ret = posix_fallocate(fd, offset, length);
#else
#error Coding error in SUPPORT_PREALLOCATION logic.
#endif
	if (ret < 0)
		return ret;
	if (opts == 0) {
		STRUCT_STAT st;
		if (do_fstat(fd, &st) < 0)
			return length;
		return st.st_blocks * S_BLKSIZE;
	}
	return 0;
}
#endif

/* Punch a hole at pos for len bytes. The current file position must be at pos and will be
 * changed to be at pos + len. */
int do_punch_hole(int fd, OFF_T pos, OFF_T len)
{
#ifdef HAVE_FALLOCATE
# ifdef HAVE_FALLOC_FL_PUNCH_HOLE
	if (fallocate(fd, FALLOC_FL_PUNCH_HOLE | FALLOC_FL_KEEP_SIZE, pos, len) == 0) {
		if (do_lseek(fd, len, SEEK_CUR) != pos + len)
			return -1;
		return 0;
	}
# endif
# ifdef HAVE_FALLOC_FL_ZERO_RANGE
	if (fallocate(fd, FALLOC_FL_ZERO_RANGE, pos, len) == 0) {
		if (do_lseek(fd, len, SEEK_CUR) != pos + len)
			return -1;
		return 0;
	}
# endif
#else
	(void)pos;
#endif
	{
		char zeros[4096];
		memset(zeros, 0, sizeof zeros);
		while (len > 0) {
			int chunk = len > (int)sizeof zeros ? (int)sizeof zeros : len;
			int wrote = write(fd, zeros, chunk);
			if (wrote <= 0) {
				if (wrote < 0 && errno == EINTR)
					continue;
				return -1;
			}
			len -= wrote;
		}
	}
	return 0;
}

int do_open_nofollow(const char *pathname, int flags)
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
	if (do_lstat(pathname, &l_st) < 0)
		return -1;
	if (S_ISLNK(l_st.st_mode)) {
		errno = ELOOP;
		return -1;
	}
	if ((fd = open(pathname, flags)) < 0)
		return fd;
	if (do_fstat(fd, &f_st) < 0) {
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
  open a file relative to a base directory. The basedir can be NULL,
  in which case the current working directory is used. The relpath
  must be a relative path. The kernel must guarantee that resolution
  cannot escape basedir (or the cwd, when basedir is NULL): no ".."
  jumps above the start, no symlinks pointing outside, no absolute
  paths, no /proc magic-link tricks.

  Symlinks *within* basedir are followed normally — earlier rsync
  versions rejected every symlink with O_NOFOLLOW on each component,
  which broke legitimate directory symlinks on the receiver side
  (https://github.com/RsyncProject/rsync/issues/715). The escape
  prevention is handled by:
    Linux 5.6+:                openat2(RESOLVE_BENEATH)
    FreeBSD 13+:               openat() with O_RESOLVE_BENEATH
    macOS 15+ / iOS 18+:       openat() with O_RESOLVE_BENEATH (same
                               flag name, picked up by the same #ifdef;
                               flag value differs from FreeBSD)
  Other systems fall back to the per-component O_NOFOLLOW walk below.

  The relpath must also not contain any ../ elements in the path.
*/

/* Returns 1 if path has any "/"-separated component that is exactly
 * "..", 0 otherwise. Used by secure_relative_open's front-door
 * validation to reject inputs that the per-component walk fallback
 * would otherwise resolve through ".." -- e.g. bare "..", "foo/..",
 * "subdir/.." -- which RESOLVE_BENEATH-equivalent kernels reject in
 * the kernel but the per-component fallback (NetBSD/OpenBSD/Solaris/
 * Cygwin/pre-5.6 Linux) does not. */
static int path_has_dotdot_component(const char *path)
{
	const char *p = path;

	while (*p) {
		const char *q;
		if (*p == '/') { p++; continue; }
		q = p;
		while (*q && *q != '/')
			q++;
		if (q - p == 2 && p[0] == '.' && p[1] == '.')
			return 1;
		p = q;
	}
	return 0;
}

#if defined(__linux__) && defined(HAVE_OPENAT2)
/* openat2(RESOLVE_BENEATH) via the raw syscall, gated on openat2_usable() so a
 * seccomp filter that traps openat2 with SIGSYS (e.g. the Android sandbox)
 * makes us report ENOSYS and fall back rather than killing the process.  Only
 * the openat2 call is gated here; a plain openat() is always safe to attempt. */
static int openat2_beneath(int dirfd, const char *path, const struct open_how *how)
{
	if (!openat2_usable()) {
		errno = ENOSYS;
		return -1;
	}
	return syscall(SYS_openat2, dirfd, path, how, sizeof *how);
}

static int secure_relative_open_linux(const char *basedir, const char *relpath, int flags, mode_t mode)
{
	struct open_how how;
	int dirfd, retfd;

	memset(&how, 0, sizeof how);
	how.flags = flags;
	how.mode = mode;
	how.resolve = RESOLVE_BENEATH | RESOLVE_NO_MAGICLINKS;

	if (basedir == NULL) {
		dirfd = AT_FDCWD;
	} else if (basedir[0] == '/') {
		/* Absolute basedir: operator-trusted (module_dir and the
		 * like). Plain openat. */
		dirfd = openat(AT_FDCWD, basedir, O_RDONLY | O_DIRECTORY);
		if (dirfd == -1)
			return -1;
	} else {
		/* Relative basedir: may be wire-influenced via
		 * --link-dest / --copy-dest / --compare-dest. Resolve it
		 * under the same RESOLVE_BENEATH guarantee as relpath, so
		 * a parent symlink on basedir cannot redirect the dirfd
		 * outside the CWD anchor. */
		struct open_how bhow;
		memset(&bhow, 0, sizeof bhow);
		bhow.flags = O_RDONLY | O_DIRECTORY;
		bhow.resolve = RESOLVE_BENEATH | RESOLVE_NO_MAGICLINKS;
		dirfd = openat2_beneath(AT_FDCWD, basedir, &bhow);
		if (dirfd == -1)
			return -1;
	}

	retfd = openat2_beneath(dirfd, relpath, &how);

	if (dirfd != AT_FDCWD)
		close(dirfd);
	return retfd;
}
#endif

#ifdef O_RESOLVE_BENEATH
/* FreeBSD 13+ and macOS 15+ (Sequoia) / iOS 18+: O_RESOLVE_BENEATH is
 * an openat() flag with the same "must not escape dirfd" semantics as
 * Linux's RESOLVE_BENEATH. The kernel rejects ".." escapes, absolute
 * symlinks, and symlinks whose target lies outside dirfd. (FreeBSD and
 * Apple use different flag bit values, but the same symbolic name.) */
static int secure_relative_open_resolve_beneath(const char *basedir, const char *relpath, int flags, mode_t mode)
{
	int dirfd, retfd;

	if (basedir == NULL) {
		dirfd = AT_FDCWD;
	} else if (basedir[0] == '/') {
		/* Absolute basedir: operator-trusted, plain openat. */
		dirfd = openat(AT_FDCWD, basedir, O_RDONLY | O_DIRECTORY);
		if (dirfd == -1)
			return -1;
	} else {
		/* Relative basedir: confine its resolution beneath CWD
		 * (see secure_relative_open_linux for the rationale). */
		dirfd = openat(AT_FDCWD, basedir, O_RDONLY | O_DIRECTORY | O_RESOLVE_BENEATH);
		if (dirfd == -1)
			return -1;
	}

	retfd = openat(dirfd, relpath, flags | O_RESOLVE_BENEATH, mode);

	if (dirfd != AT_FDCWD)
		close(dirfd);
	return retfd;
}
#endif

/* The logical current directory (maintained by change_dir() in util1.c).
 * Defined here -- rather than in util1.c -- so the test helpers that link
 * syscall.o but not util1.o (tls, trimslash) get the definition without a
 * weak-symbol fallback, which is not portable to PE/COFF targets (Cygwin). */
char curr_dir[MAXPATHLEN];
unsigned int curr_dir_len;

int secure_relative_open(const char *basedir, const char *relpath, int flags, mode_t mode)
{
	extern int am_daemon, am_chrooted;
	extern char *module_dir;
	extern unsigned int module_dirlen;
	char modrel_buf[MAXPATHLEN];
	int reanchored = 0;

	if (!relpath || relpath[0] == '/') {
		// must be a relative path
		errno = EINVAL;
		return -1;
	}

	/* Sanitizing daemon only (am_daemon && !am_chrooted).  Here we have chdir'd
	 * into a sub-dir of the module (the transfer destination), so a relative
	 * alt-dest like "../01" may legitimately climb to a sibling that is still
	 * inside the module (#915).  Confining beneath the cwd would reject that
	 * climb.  Re-anchor at the module root -- the real trust boundary -- by
	 * prefixing the cwd's module-relative path (from rsync's logical curr_dir[],
	 * a guaranteed lexical prefix of module_dir, unlike getcwd()) and resolving
	 * beneath module_dir; RESOLVE_BENEATH then allows in-module climbs and still
	 * rejects escapes.  Only for paths that contain "..".  module_dirlen is 0 for
	 * a `path = /` module (clientserver.c), so we gate on module_dir, not its
	 * length, to cover that case too -- the prefix check below treats
	 * module_dirlen 0 as "module root is /". */
	if (am_daemon && !am_chrooted
	 && module_dir && module_dir[0] == '/'
	 && (basedir == NULL || basedir[0] != '/')
	 && (path_has_dotdot_component(relpath)
	  || (basedir && path_has_dotdot_component(basedir)))) {
		const char *p;
		int n;
		if (curr_dir_len >= module_dirlen
		 && strncmp(curr_dir, module_dir, module_dirlen) == 0
		 && (curr_dir[module_dirlen] == '\0' || curr_dir[module_dirlen] == '/')) {
			for (p = curr_dir + module_dirlen; *p == '/'; p++) {}
			if (basedir)
				n = snprintf(modrel_buf, sizeof modrel_buf, "%s%s%s/%s",
					     p, *p ? "/" : "", basedir, relpath);
			else
				n = snprintf(modrel_buf, sizeof modrel_buf, "%s%s%s",
					     p, *p ? "/" : "", relpath);
			if (n < 0 || n >= (int)sizeof modrel_buf) {
				errno = ENAMETOOLONG;
				return -1;
			}
			basedir = module_dir;	/* absolute, operator-trusted anchor */
			relpath = modrel_buf;
			reanchored = 1;
		}
		/* else: cwd not under module root as expected -- fall through to the
		 * front-door rejection below (fail safe). */
	}

	/* Reject any path with a literal ".." component (bare "..",
	 * "../foo", "foo/..", "foo/../bar", "subdir/.."). The previous
	 * substring-based check caught only "../" prefix and "/../"
	 * substring; bare ".." and trailing "/.." escape on the per-
	 * component walk fallback used by NetBSD/OpenBSD/Solaris/Cygwin
	 * and pre-5.6 Linux. RESOLVE_BENEATH on Linux/FreeBSD/macOS
	 * catches some of these in-kernel with EXDEV, but the front
	 * door must reject them consistently with EINVAL across all
	 * platforms so callers can rely on the validation.  Skipped for a
	 * re-anchored path: its ".." is deliberate, stays within the module,
	 * and is adjudicated by RESOLVE_BENEATH below (the portable fallback
	 * re-rejects it -- see there). */
	if (!reanchored) {
		if (path_has_dotdot_component(relpath)) {
			errno = EINVAL;
			return -1;
		}
		if (basedir && basedir[0] != '/' && path_has_dotdot_component(basedir)) {
			errno = EINVAL;
			return -1;
		}
	}

#if defined(__linux__) && defined(HAVE_OPENAT2)
	{
		int fd = secure_relative_open_linux(basedir, relpath, flags, mode);
		/* ENOSYS = kernel < 5.6 doesn't have the syscall even though
		 * glibc/kernel-headers do; fall through to the portable path. */
		if (fd != -1 || errno != ENOSYS)
			return fd;
	}
#endif

#ifdef O_RESOLVE_BENEATH
	return secure_relative_open_resolve_beneath(basedir, relpath, flags, mode);
#endif

	/* Portable fallback only (no kernel RESOLVE_BENEATH): the per-component
	 * O_NOFOLLOW walk below can't adjudicate ".." safely, so reject it here --
	 * even for a re-anchored path.  This re-breaks --link-dest=../01 on
	 * openat2/O_RESOLVE_BENEATH-less platforms (NetBSD/OpenBSD/Solaris/Cygwin/
	 * pre-5.6 Linux), trading function for safety; on the kernel paths above
	 * RESOLVE_BENEATH already allowed the in-module climb. */
	if (path_has_dotdot_component(relpath)) {
		errno = EINVAL;
		return -1;
	}
	if (basedir && basedir[0] != '/' && path_has_dotdot_component(basedir)) {
		errno = EINVAL;
		return -1;
	}

#if !defined(O_NOFOLLOW) || !defined(O_DIRECTORY) || !defined(AT_FDCWD)
	// really old system, all we can do is live with the risks
	if (!basedir) {
		return open(relpath, flags, mode);
	}
	char fullpath[MAXPATHLEN];
	pathjoin(fullpath, sizeof fullpath, basedir, relpath);
	return open(fullpath, flags, mode);
#else
	int dirfd = AT_FDCWD;
	if (basedir != NULL) {
		if (basedir[0] == '/') {
			/* Absolute basedir: operator-trusted, plain openat. */
			dirfd = openat(AT_FDCWD, basedir, O_RDONLY | O_DIRECTORY);
			if (dirfd == -1) {
				return -1;
			}
		} else {
			/* Relative basedir: walk it component-by-component
			 * with O_NOFOLLOW. This is the per-component
			 * RESOLVE_BENEATH equivalent for platforms without
			 * kernel-supported confinement, and matches the
			 * relpath walk below. Symlinks in basedir are
			 * rejected outright on this fallback path; the
			 * Linux openat2 / O_RESOLVE_BENEATH paths above
			 * still allow within-tree symlinks. */
			char *bcopy = my_strdup(basedir, __FILE__, __LINE__);
			if (!bcopy)
				return -1;
			for (const char *part = strtok(bcopy, "/");
			     part != NULL;
			     part = strtok(NULL, "/"))
			{
				int next_fd = openat(dirfd, part, O_RDONLY | O_DIRECTORY | O_NOFOLLOW);
				if (next_fd == -1) {
					int save_errno = errno;
					if (dirfd != AT_FDCWD) close(dirfd);
					free(bcopy);
					errno = save_errno;
					return -1;
				}
				if (dirfd != AT_FDCWD) close(dirfd);
				dirfd = next_fd;
			}
			free(bcopy);
		}
	}
	int retfd = -1;

	char *path_copy = my_strdup(relpath, __FILE__, __LINE__);
	if (!path_copy) {
		if (dirfd != AT_FDCWD) close(dirfd);
		return -1;
	}
	
	for (const char *part = strtok(path_copy, "/");
	     part != NULL;
	     part = strtok(NULL, "/"))
	{
		int next_fd = openat(dirfd, part, O_RDONLY | O_DIRECTORY | O_NOFOLLOW);
		if (next_fd == -1 && errno == ENOTDIR) {
			if (strtok(NULL, "/") != NULL) {
				// this is not the last component of the path
				errno = ELOOP;
				goto cleanup;
			}
			// this could be the last component of the path, try as a file
			retfd = openat(dirfd, part, flags | O_NOFOLLOW, mode);
			goto cleanup;
		}
		if (next_fd == -1) {
			goto cleanup;
		}
		if (dirfd != AT_FDCWD) close(dirfd);
		dirfd = next_fd;
	}

	/* All components walked as directories. If the caller asked for
	 * O_DIRECTORY, return the dirfd we built up; otherwise the path
	 * resolved to a directory but the caller wanted a regular file. */
	if ((flags & O_DIRECTORY) && dirfd != AT_FDCWD) {
		retfd = dirfd;
		dirfd = AT_FDCWD;
		goto cleanup;
	}
	errno = EISDIR;

cleanup:
	free(path_copy);
	if (dirfd != AT_FDCWD) {
		close(dirfd);
	}
	return retfd;
#endif // O_NOFOLLOW, O_DIRECTORY
}

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

/*
  Secure version of mkstemp that prevents symlink attacks on parent directories.
  Like secure_relative_open(), this walks the path checking each component
  with O_NOFOLLOW to prevent TOCTOU race conditions.

  The template may be relative or absolute, but must not contain ../ components.
  Returns fd on success, -1 on error.
*/
int secure_mkstemp(char *template, mode_t perms)
{
#if !defined(O_NOFOLLOW) || !defined(O_DIRECTORY) || !defined(AT_FDCWD)
	/* Fall back to regular mkstemp on old systems */
	return do_mkstemp(template, perms);
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

	/* For absolute paths, start the secure walk from "/" rather than CWD. */
	if (template[0] == '/') {
		dirfd = open("/", O_RDONLY | O_DIRECTORY | O_NOFOLLOW);
		if (dirfd < 0)
			return -1;
	}

	/* Find the last slash to separate directory from filename */
	lastslash = strrchr(template, '/');
	if (lastslash) {
		char *path_copy = my_strdup(template, __FILE__, __LINE__);
		if (!path_copy)
			return -1;

		/* Null-terminate at the last slash to get directory part */
		path_copy[lastslash - template] = '\0';

		/* Walk the directory path securely */
		for (const char *part = strtok(path_copy, "/");
		     part != NULL;
		     part = strtok(NULL, "/"))
		{
			int next_fd = openat(dirfd, part, O_RDONLY | O_DIRECTORY | O_NOFOLLOW);
			if (next_fd == -1) {
				int save_errno = errno;
				free(path_copy);
				if (dirfd != AT_FDCWD) close(dirfd);
				errno = (save_errno == ELOOP) ? ELOOP : save_errno;
				return -1;
			}
			if (dirfd != AT_FDCWD) close(dirfd);
			dirfd = next_fd;
		}
		free(path_copy);
	}

	/* Now create the temp file in the securely-opened directory */
	perms |= S_IWUSR;

	/* Generate unique filename - we need to modify the template in place */
	char *filename = lastslash ? lastslash + 1 : template;
	size_t filename_len = strlen(filename);

	if (filename_len < 6) {
		if (dirfd != AT_FDCWD) close(dirfd);
		errno = EINVAL;
		return -1;
	}
	char *suffix = filename + filename_len - 6; /* Points to XXXXXX */
	if (strcmp(suffix, "XXXXXX") != 0) {
		if (dirfd != AT_FDCWD) close(dirfd);
		errno = EINVAL;
		return -1;
	}

	/* Try random suffixes until we find one that works */
	static const char letters[] = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789";
	for (int tries = 0; tries < 100; tries++) {
		unsigned char rbytes[6];
		rand_bytes(rbytes, sizeof(rbytes));
		for (int i = 0; i < 6; i++)
			suffix[i] = letters[rbytes[i] % (sizeof(letters) - 1)];

		fd = openat(dirfd, filename, O_RDWR | O_CREAT | O_EXCL | O_NOFOLLOW, perms);
		if (fd >= 0)
			break;
		if (errno != EEXIST) {
			if (dirfd != AT_FDCWD) close(dirfd);
			return -1;
		}
	}

	if (fd >= 0) {
		if (fchmod(fd, perms) != 0 && preserve_perms) {
			int errno_save = errno;
			close(fd);
			unlinkat(dirfd, filename, 0);
			if (dirfd != AT_FDCWD) close(dirfd);
			errno = errno_save;
			return -1;
		}
#if defined HAVE_SETMODE && O_BINARY
		setmode(fd, O_BINARY);
#endif
	}

	if (dirfd != AT_FDCWD) close(dirfd);
	return fd;
#endif
}

/*
  varient of do_open/do_open_nofollow which does do_open() if the
  copy_links or copy_unsafe_links options are set and does
  do_open_nofollow() otherwise

  This is used to prevent a race condition where an attacker could be
  switching a file between being a symlink and being a normal file

  The open is always done with O_RDONLY flags
 */
int do_open_checklinks(const char *pathname)
{
	if (copy_links || copy_unsafe_links) {
		return do_open(pathname, O_RDONLY, 0);
	}
	return do_open_nofollow(pathname, O_RDONLY);
}

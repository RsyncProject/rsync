/*
 * vfs/chmod.c - chmod wrappers (path, parent-resolved, held-dirfd).
 *
 * Includes the platform-specific lchmod/setattrlist/SYS_fchmodat2 handling and
 * the leaf-safe do_fchmodat_nofollow helper.  Moved verbatim out of syscall.c.
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

#ifdef HAVE_CHMOD
int vfs_chmod(const char *path, mode_t mode)
{
	static int switch_step = 0;
	int code;

	if (dry_run) return 0;
	RETURN_ERROR_IF_RO_OR_LO;
	RETURN_ERROR_IF_NULL(path);

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

/* chmod `name` relative to dfd without following a final-component symlink.
 * The held parent fd confines the ancestors; this closes the leaf race (an
 * attacker swapping the leaf to a symlink that fchmodat(...,0) would follow out
 * of the tree).
 *
 * Never follows the leaf: a regular file, dir or FIFO is pinned via
 * openat(O_NOFOLLOW) and chmod'd with fchmod() (leaf-safe, every kernel, and
 * fakeroot-wrappable unlike the raw fchmodat2() syscall); a symlink leaf is
 * refused (ELOOP).  Other types or an open failure fall to
 * fchmodat(AT_SYMLINK_NOFOLLOW) (a real no-follow chmod on glibc>=2.32 /
 * Linux>=6.6), then the raw fchmodat2() syscall.  If no no-follow primitive
 * exists we skip with a warning rather than follow the leaf. */
static int do_fchmodat_nofollow(int dfd, const char *name, mode_t mode)
{
	mode &= CHMOD_BITS;
#if defined AT_FDCWD && defined AT_SYMLINK_NOFOLLOW
# if defined __linux__
	{
		STRUCT_STAT st;
		if (vfs_lstat_atfd(dfd, name, &st) < 0)
			return -1;
		if (S_ISLNK(st.st_mode)) {
			errno = ELOOP;	/* refuse to chmod through a symlink leaf */
			return -1;
		}
		if (S_ISREG(st.st_mode) || S_ISDIR(st.st_mode) || S_ISFIFO(st.st_mode)) {
			int fd = openat(dfd, name, O_RDONLY | O_NOFOLLOW | O_NONBLOCK | O_NOCTTY | O_CLOEXEC);
			if (fd >= 0) {
				int r = fchmod(fd, mode), e = errno;
				close(fd);
				errno = e;
				return r;
			}
			if (errno == ELOOP)
				return -1;	/* raced to a symlink: refuse */
			/* otherwise (e.g. EACCES on an unreadable file) fall through */
		}
	}
	{
		int r = fchmodat(dfd, name, mode, AT_SYMLINK_NOFOLLOW);
		if (r == 0)
			return 0;
		if (errno != ENOTSUP && errno != EOPNOTSUPP && errno != ENOSYS)
			return r;	/* a real error (EPERM, ENOENT, ...) */
	}
#  ifdef SYS_fchmodat2
	{
		int r = syscall(SYS_fchmodat2, dfd, name, (unsigned int)mode, AT_SYMLINK_NOFOLLOW);
		if (r == 0)
			return 0;
		if (errno != ENOSYS && errno != EPERM && errno != EOPNOTSUPP)
			return r;
	}
#  endif
	/* No symlink-safe chmod primitive here: skip rather than follow the leaf. */
	rprintf(FWARNING, "vfs_chmod: no symlink-safe chmod for \"%s\"; mode not set\n", name);
	return 1;
# else
	return fchmodat(dfd, name, mode, AT_SYMLINK_NOFOLLOW);
# endif
#else
	/* No symlink-safe chmod primitive here: skip rather than follow the leaf. */
	rprintf(FWARNING, "vfs_chmod: no symlink-safe chmod for \"%s\"; mode not set\n", name);
	return 1;
#endif
}

/*
  Symlink-race-safe variant of vfs_chmod() for receiver-side use.

  Threat model: on a daemon running with "use chroot = no" (the prerequisite
  for CVE-2026-29518), a local attacker can race a symlink swap of one of
  the parent directory components of a path the receiver is about to chmod.
  Because chmod() resolves symlinks at every component, the swap redirects
  the chmod outside the receiver's confinement.

  Defence: open the *parent* directory of fname under vfs_resolve_open()
  (a portable per-component O_NOFOLLOW walk on held parent dirfds) and do
  fchmodat() against that dirfd. A symlink substituted into one of the parent
  components is then either followed within the tree (legitimate dir-symlinks
  still work) or rejected (escape attempts fail).

  Final-component handling matches vfs_chmod(): fchmodat() with flag 0
  follows a symlink at the final component, which is the same behaviour as
  chmod() and matches every current call site (the file being chmod'd is
  one the receiver itself just created or transferred). For the rare case
  where the caller wants to chmod a symlink-as-an-object (S_ISLNK in the
  mode bits), we fall through to vfs_chmod() which has portability code for
  that case.

  Falls back to vfs_chmod() for absolute paths and for paths with no parent
  component, where there is nothing to protect against.
*/
int vfs_chmod_at(const char *fname, mode_t mode)
{
#ifdef AT_FDCWD
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
	 * vfs_chmod() to avoid the dirfd-open overhead on every call. */
	if (!vfs_relpath_active())
		return vfs_chmod(fname, mode);

	if (!fname || !*fname || *fname == '/' || S_ISLNK(mode))
		return vfs_chmod(fname, mode);

	slash = strrchr(fname, '/');
	if (!slash)
		return vfs_chmod(fname, mode);

	dlen = slash - fname;
	if (dlen >= sizeof dirpath) {
		errno = ENAMETOOLONG;
		return -1;
	}
	memcpy(dirpath, fname, dlen);
	dirpath[dlen] = '\0';
	bname = slash + 1;

	dfd = vfs_resolve_open(NULL, dirpath, O_RDONLY | O_DIRECTORY, 0);
	if (dfd < 0)
		return -1;

	ret = do_fchmodat_nofollow(dfd, bname, mode);
	e = errno;
	close(dfd);
	errno = e;
	return ret;
#else
	return vfs_chmod(fname, mode);
#endif
}
#endif

#ifdef HAVE_CHMOD
int vfs_chmod_atfd(int dfd, const char *name, mode_t mode)
{
#ifdef AT_FDCWD
	if (dry_run) return 0;
	RETURN_ERROR_IF_RO_OR_LO;
	/* Do not follow a final-component symlink (closes the leaf race; the
	 * held parent dfd already confines the ancestors).  A symlink-as-object
	 * (S_ISLNK(mode)) is still handled by the caller via the full-path
	 * vfs_chmod() lchmod/setattrlist code, exactly as vfs_chmod_at() does. */
	return do_fchmodat_nofollow(dfd, name, mode);
#else
	(void)dfd; (void)name; (void)mode;
	errno = ENOSYS;
	return -1;
#endif
}
#endif

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

#ifdef HAVE_SYS_UN_H
#include <sys/un.h>	/* for the socket+bind() fallback in do_mknod() */
#endif
#ifdef HAVE_SYS_ATTR_H
#include <sys/attr.h>
#endif

#if defined HAVE_SYS_FALLOCATE && !defined HAVE_FALLOCATE
#include <sys/syscall.h>
#endif

#ifdef __linux__
#include <sys/syscall.h>	/* SYS_fchmodat2 / SYS_fallocate raw-syscall wrappers */
#endif

#include "ifuncs.h"
#include "vfs/vfs_internal.h"





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





int do_lchown(const char *path, uid_t owner, gid_t group)
{
	if (dry_run) return 0;
	RETURN_ERROR_IF_RO_OR_LO;
	RETURN_ERROR_IF_NULL(path);
#ifndef HAVE_LCHOWN
#define lchown chown
#endif
	return lchown(path, owner, group);
}

/*
  Symlink-race-safe variant of do_lchown() for receiver-side use. See the
  comment on vfs_chmod_at() for the threat model and design rationale.

  Resolves the parent directory under vfs_resolve_open() and invokes
  fchownat(..., AT_SYMLINK_NOFOLLOW) against that dirfd, so that an
  attacker who substitutes a symlink into one of the parent components
  cannot redirect the chown outside the receiver's confinement. The
  AT_SYMLINK_NOFOLLOW flag matches lchown()'s "do not follow a final-
  component symlink" semantics.

  Falls through to do_lchown() in the dry-run / non-daemon / chrooted /
  absolute-path / no-parent cases, identical to vfs_chmod_at().
*/
int do_lchown_at(const char *fname, uid_t owner, gid_t group)
{
#ifdef AT_FDCWD
	char dirpath[MAXPATHLEN];
	const char *bname;
	const char *slash;
	int dfd, ret, e;
	size_t dlen;

	if (dry_run) return 0;
	RETURN_ERROR_IF_RO_OR_LO;

	if (!vfs_relpath_active())
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

	dfd = vfs_resolve_open(NULL, dirpath, O_RDONLY | O_DIRECTORY, 0);
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
  Symlink-race-safe variant of do_mknod() for receiver-side use. See
  the comment on vfs_chmod_at() for the threat model. Defence: open
  the parent of pathname under vfs_resolve_open() and use
  mknodat() against that dirfd. mknodat() covers both regular-file
  (S_IFREG with dev=0) and FIFO (S_IFIFO) and device-node creation.

  A top-level (no-slash) pathname has no parent to confine, so it uses
  AT_FDCWD; the final component is still protected (mknodat/mkfifoat do
  not follow it, and the fake-super openat() uses O_NOFOLLOW).

  Fake-super (am_root < 0) is handled inline against the (secure or
  AT_FDCWD) dirfd: it creates a regular empty file (the same file-as-
  metadata-placeholder pattern do_mknod uses) via openat() with
  O_NOFOLLOW so a pre-planted symlink at the basename can't redirect
  the file creation -- top-level paths included (the previous code fell
  through to the bare-path do_mknod() there, whose plain open() followed
  such a symlink). On Linux, sockets are recreated with mknodat() like any
  other special file; on systems where mknod() can't create sockets the
  at-variant fails instead of re-resolving an unsafe parent.
*/
int do_mknod_at(const char *pathname, mode_t mode, dev_t dev)
{
	/* HAVE_MKNODAT: older Darwin declares AT_FDCWD but not mknodat(), so
	 * the at-variant won't build there; fall back to do_mknod() (#896). */
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
			return do_mknod(pathname, mode, dev);
		dfd = vfs_owner_walk_parent(pathname, &bname);
		if (dfd < 0)
			return -1;
		if (am_root < 0) {
			/* Fake-super represents a special file with an inert regular
			 * placeholder.  Keep that representation when the destination
			 * is an operator path, but create it relative to the verified
			 * parent so the confinement guarantee is unchanged. */
			int fd = openat(dfd, bname,
				O_WRONLY | O_CREAT | O_TRUNC | O_NOFOLLOW,
				S_IWUSR | S_IRUSR);
			ret = fd < 0 ? -1 : close(fd);
		} else {
			ret = mknodat(dfd, bname, mode, dev);
		}
		if (ret < 0 && am_root >= 0) {
			/* mknodat() can't make a FIFO/socket on the BSDs/macOS/
			 * Solaris (EINVAL); retry race-safely on the held dirfd,
			 * mirroring the secure-relpath path below.  Without this a
			 * FIFO backup to an operator --backup-dir fails there. */
#ifdef HAVE_MKFIFOAT
			if (S_ISFIFO(mode))
				ret = mkfifoat(dfd, bname, mode);
			else
#endif
			if (S_ISSOCK(mode))
				errno = EOPNOTSUPP; /* no dirfd-relative socket bind */
		}
		e = errno;
		close(dfd);
		errno = e;
		return ret;
	}
#endif

	if (!vfs_relpath_active())
		return do_mknod(pathname, mode, dev);

	if (!pathname || !*pathname || *pathname == '/')
		return do_mknod(pathname, mode, dev);

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
		/* For --fake-super, do_mknod creates a regular empty
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
	 * the capability is filesystem-dependent (see do_mknod()). */
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
			 * do_mknod(), but a nested one fails safe rather than
			 * re-resolve a potentially unsafe parent. */
			if (dfd == AT_FDCWD)
				ret = do_mknod(pathname, mode, dev);
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
	return do_mknod(pathname, mode, dev);
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
	struct attrlist attrList;
	struct timespec ts[2];

	if (dry_run) return 0;
	RETURN_ERROR_IF_RO_OR_LO;

	/* setattrlist() takes a raw path and follows parent symlinks
	 * (FSOPT_NOFOLLOW only blocks the final component). When hardened
	 * resolution is active -- vfs_relpath_active(): any non-chroot
	 * daemon/receiver module, plus a /./ inner-module chroot -- return
	 * ENOSYS so set_times()' tier walk falls through to do_utimensat_at(),
	 * which routes the update through a secure parent dirfd. The attribute
	 * set this would have used (ATTR_CMN_MODTIME / ATTR_CMN_ACCTIME) is the
	 * same set utimensat() handles, so no functionality is lost. */
	if (vfs_relpath_active()) {
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
	struct attrlist attrList;
	struct timespec ts;

	if (dry_run) return 0;
	RETURN_ERROR_IF_RO_OR_LO;

	/* setattrlist() is path-based and follows parent symlinks
	 * (FSOPT_NOFOLLOW only blocks the final component), and macOS has no
	 * at-aware variant targeting ATTR_CMN_CRTIME.  As with POSIX ACLs where
	 * the OS offers no race-safe primitive, we keep --crtimes functional
	 * (daemon and non-daemon) and accept the parent-component symlink race
	 * as a documented residual rather than dropping crtime.  A daemon
	 * operator who does not want that residual can disable the feature with
	 * "refuse options = crtimes" in rsyncd.conf. */
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
	static struct create_time attrBuf;
	struct attrlist attrList;

	(void)stp;
	/* getattrlist() is path-based and follows parent symlinks; like
	 * do_setattrlist_crtime() there is no race-safe variant, so reading the
	 * source crtime stays functional and the parent-component symlink race
	 * is an accepted residual (refusable via "refuse options = crtimes"). */
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
	RETURN_ERROR_IF_NULL(path);

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
  See the comment on vfs_chmod_at() for the threat model. utimes()
  resolves parent components and follows a final-component symlink;
  lutimes() doesn't follow the final component but still resolves
  parents. Either way, a parent-symlink swap can redirect the
  timestamp update outside the module. Defence: open the parent of
  path under vfs_resolve_open() and call utimensat() with
  AT_SYMLINK_NOFOLLOW against that dirfd.

  Falls through to do_utimensat() in the same dry-run / non-daemon /
  chrooted / no-parent / absolute-path cases as the other wrappers.
  Returns -1 with errno=ENOSYS on systems without utimensat()
  (caller is expected to fall back to the legacy tier walk).
*/
int do_utimensat_at(const char *path, STRUCT_STAT *stp)
{
#ifdef AT_FDCWD
	struct timespec t[2];
	char dirpath[MAXPATHLEN];
	const char *bname;
	const char *slash;
	int dfd, ret, e;
	size_t dlen;

	if (dry_run) return 0;
	RETURN_ERROR_IF_RO_OR_LO;

	if (!vfs_relpath_active())
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

	dfd = vfs_resolve_open(NULL, dirpath, O_RDONLY | O_DIRECTORY, 0);
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
	/* FALLOC_FL_KEEP_SIZE lets --preallocate/--inplace keep the file size at 0
	 * until data is written, but a later hole-punch (for --sparse) can only
	 * deallocate blocks that lie within the file's size -- with KEEP_SIZE the
	 * reserved blocks sit beyond EOF and the punch silently does nothing,
	 * leaving the file fully allocated.  So when holes will also be punched,
	 * preallocate at full size instead (write_sparse then punches the nulls). */
	int opts = (inplace || preallocate_files) && sparse_files <= 0 ? DO_FALLOC_OPTIONS : 0;
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
		if (vfs_fstat(fd, &st) < 0)
			return length;
		return st.st_blocks * S_BLKSIZE;
	}
	/* With FALLOC_FL_KEEP_SIZE the blocks for [0, length) are reserved even
	 * though the file size stays put.  Return that reserved length (not 0) so
	 * the caller's preallocated_len is meaningful: write_sparse() needs it to
	 * choose do_punch_hole() over a plain lseek() when turning a null run into
	 * a hole, and the receiver uses it to trim any over-preallocation.  (A
	 * stray 0 here, from 2019's switch to KEEP_SIZE, is why --preallocate
	 * --sparse stopped producing sparse files.) */
	return length;
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


/* The logical current directory (maintained by change_dir() in util1.c).
 * Defined here -- rather than in util1.c -- so the test helpers that link
 * syscall.o but not util1.o (tls, trimslash) get the definition without a
 * weak-symbol fallback, which is not portable to PE/COFF targets (Cygwin). */




int do_lchown_atfd(int dfd, const char *name, uid_t owner, gid_t group)
{
#ifdef AT_FDCWD
	if (dry_run) return 0;
	RETURN_ERROR_IF_RO_OR_LO;
	return fchownat(dfd, name, owner, group, AT_SYMLINK_NOFOLLOW);
#else
	(void)dfd; (void)name; (void)owner; (void)group;
	errno = ENOSYS;
	return -1;
#endif
}

/* Mode/owner on an already-open fd (no path, no symlink to follow): the
 * race-free way to set metadata on a cross-tree operator-path leaf that was
 * pinned with O_NOFOLLOW.  See set_file_attrs(). */
int do_fchown(int fd, uid_t owner, gid_t group)
{
	if (dry_run) return 0;
	RETURN_ERROR_IF_RO_OR_LO;
	return fchown(fd, owner, group);
}

#ifdef HAVE_CHMOD
int do_fchmod(int fd, mode_t mode)
{
	if (dry_run) return 0;
	RETURN_ERROR_IF_RO_OR_LO;
	return fchmod(fd, mode);
}
#endif

#ifdef HAVE_FUTIMENS
/* Set times on an already-open fd (the race-free counterpart for a pinned
 * cross-tree operator leaf -- see set_file_attrs()). */
int do_futimens(int fd, STRUCT_STAT *stp)
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
	return futimens(fd, t);
}
#endif

#ifdef HAVE_UTIMENSAT
int do_utimensat_atfd(int dfd, const char *name, STRUCT_STAT *stp)
{
#ifdef AT_FDCWD
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
	return utimensat(dfd, name, t, AT_SYMLINK_NOFOLLOW);
#else
	(void)dfd; (void)name; (void)stp;
	errno = ENOSYS;
	return -1;
#endif
}
#endif



int do_mknod_atfd(int dfd, const char *name, mode_t mode, dev_t dev)
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
	 * specific primitive (see do_mknod()).  HAVE_MKNODAT, not HAVE_MKNOD:
	 * older Darwin has mknod() but not mknodat(), so keying off the former
	 * compiles a call that then fails to link (#161). */
#ifdef HAVE_MKNODAT
	if (mknodat(dfd, name, mode, dev) == 0)
		return 0;
#endif
#ifdef HAVE_MKFIFOAT
	if (S_ISFIFO(mode))
		return mkfifoat(dfd, name, mode);
#endif
	if (S_ISSOCK(mode)) {
		/* No dirfd-relative socket bind without /proc/self/fd; fail safe.
		 * (The generator routes sockets to do_mknod_at(), not here.) */
		errno = EOPNOTSUPP;
		return -1;
	}
#ifdef HAVE_MKNODAT
	return -1;	/* mknodat()'s errno (regular/device node) */
#else
	/* Must match the guard above: reporting "mknodat()'s errno" where the
	 * call was never compiled would return a stale errno. */
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



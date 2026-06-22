/*
 * vfs/times.c - timestamp-setting wrappers (utimensat/lutimes/utimes/utime)
 * plus the macOS setattrlist crtime path and the Cygwin SetFileTime path.
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
#ifdef HAVE_SYS_ATTR_H
#include <sys/attr.h>
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

#ifdef HAVE_SETATTRLIST
int vfs_setattrlist_times(const char *path, STRUCT_STAT *stp)
{
	struct attrlist attrList;
	struct timespec ts[2];

	if (dry_run) return 0;
	RETURN_ERROR_IF_RO_OR_LO;

	/* setattrlist() takes a raw path and follows parent symlinks
	 * (FSOPT_NOFOLLOW only blocks the final component). When hardened
	 * resolution is active -- vfs_relpath_active(): any non-chroot
	 * daemon/receiver module, plus a /./ inner-module chroot -- return
	 * ENOSYS so set_times()' tier walk falls through to vfs_utimensat_at(),
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
int vfs_setattrlist_crtime(const char *path, time_t crtime)
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
time_t vfs_get_create_time(const char *path, STRUCT_STAT *stp)
{
#ifdef HAVE_GETATTRLIST
	static struct create_time attrBuf;
	struct attrlist attrList;

	(void)stp;
	/* getattrlist() is path-based and follows parent symlinks; like
	 * vfs_setattrlist_crtime() there is no race-safe variant, so reading the
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
int vfs_SetFileTime(const char *path, time_t crtime)
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
int vfs_utimensat(const char *path, STRUCT_STAT *stp)
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
  Symlink-race-safe variant of vfs_utimensat() for receiver-side use.
  See the comment on vfs__chmod_secure() for the threat model. utimes()
  resolves parent components and follows a final-component symlink;
  lutimes() doesn't follow the final component but still resolves
  parents. Either way, a parent-symlink swap can redirect the
  timestamp update outside the module. Defence: open the parent of
  path under vfs_resolve_open() and call utimensat() with
  AT_SYMLINK_NOFOLLOW against that dirfd.

  Falls through to vfs_utimensat() in the same dry-run / non-daemon /
  chrooted / no-parent / absolute-path cases as the other wrappers.
  Returns -1 with errno=ENOSYS on systems without utimensat()
  (caller is expected to fall back to the legacy tier walk).
*/
int vfs_utimensat_at(const char *path, STRUCT_STAT *stp)
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
		return vfs_utimensat(path, stp);

	if (!path || !*path || *path == '/')
		return vfs_utimensat(path, stp);

	slash = strrchr(path, '/');
	if (!slash)
		return vfs_utimensat(path, stp);

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
	return vfs_utimensat(path, stp);
#endif
}
#endif

#ifdef HAVE_LUTIMES
int vfs_lutimes(const char *path, STRUCT_STAT *stp)
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
int vfs_utimes(const char *path, STRUCT_STAT *stp)
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
int vfs_utime(const char *path, STRUCT_STAT *stp)
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

#ifdef HAVE_UTIMENSAT
int vfs_utimensat_atfd(int dfd, const char *name, STRUCT_STAT *stp)
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

/*
 * vfs/copy_file.c - compound VFS op: copy a file's contents (and, for
 * --xattrs, its xattrs) to a new destination.  Layered on the vfs_* open/
 * read/write primitives; calls out to the metadata layer (copy_xattrs) for
 * the held-fd xattr copy.
 *
 * Moved out of util1.c as part of the VFS compound layer.
 *
 * Copyright (C) 1996-2022 Andrew Tridgell, Paul Mackerras, Wayne Davison
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

extern int do_fsync;
extern int preallocate_files;
extern int preserve_xattrs;

/* Read @p len bytes at @p ptr from descriptor @p desc, retrying if interrupted.
 * Returns the number of bytes read (0 = EOF), or <0 on error.  Derived from GNU
 * C's cccp.c. */
static int safe_read(int desc, char *ptr, size_t len)
{
	int n_chars;

	if (len == 0)
		return len;

	do {
		n_chars = read(desc, ptr, len);
	} while (n_chars < 0 && errno == EINTR);

	return n_chars;
}

/* Remove existing file @dest and reopen, creating a new file with @mode.
 * vfs_flags carries the resolution policy (VFS_OPERATOR_PATH for an operator
 * dest) to the create; the robust_unlink still reads it from the (transitional)
 * operator-path global. */
static int unlink_and_reopen(const char *dest, mode_t mode, int vfs_flags)
{
	int ofd;

	if (robust_unlink(dest, vfs_flags) && errno != ENOENT) {
		int save_errno = errno;
		rsyserr(FERROR_XFER, errno, "unlink %s", full_fname(dest));
		errno = save_errno;
		return -1;
	}

#ifdef SUPPORT_XATTRS
	if (preserve_xattrs)
		mode |= S_IWUSR;
#endif
	mode &= INITACCESSPERMS;
	/* Use vfs_open_at so the create/truncate goes through a secure
	 * parent dirfd in the daemon-no-chroot deployment. Otherwise
	 * an attacker could swap a parent component with a symlink in
	 * the window between robust_unlink (which uses vfs_unlink,
	 * already secure) and the create here, and redirect the new
	 * file outside the module. */
	if ((ofd = vfs_open_at(dest, O_WRONLY | O_CREAT | O_TRUNC | O_EXCL, mode, vfs_flags)) < 0) {
		int save_errno = errno;
		rsyserr(FERROR_XFER, save_errno, "open %s", full_fname(dest));
		errno = save_errno;
		return -1;
	}
	return ofd;
}

/* Copy contents of file @source to file @dest with mode @mode.
 *
 * If @tmpfilefd is < 0, copy_file unlinks @dest and then opens a new
 * file with name @dest.
 *
 * Otherwise, copy_file writes to and closes the provided file
 * descriptor.
 *
 * In either case, if --xattrs are being preserved, the dest file will
 * have its xattrs set from the source file.
 *
 * This is used in conjunction with the --temp-dir, --backup, and
 * --copy-dest options. */
int copy_file(const char *source, const char *dest, int tmpfilefd, mode_t mode, int vfs_flags)
{
	int ifd, ofd;
	char buf[1024 * 8];
	int len;   /* Number of bytes read into `buf'. */
	OFF_T prealloc_len = 0, offset = 0;

	/* For any hardened (non-chrooted) receiver, route the source open through
	 * vfs_resolve_open so a parent-symlink on the source path (e.g.
	 * --copy-dest=cd where cd is a symlink to an outside directory) cannot
	 * redirect the read to a file the attacker should not see.  Plain
	 * vfs_open_nofollow only refuses a final-component symlink; parents are
	 * still followed.  (An absolute source is operator-trusted -- e.g. an
	 * absolutized basis dir -- and uses vfs_open_nofollow.) */
	if (vfs_relpath_active() && source && *source && source[0] != '/')
		ifd = vfs_resolve_open(NULL, source, O_RDONLY | O_NOFOLLOW, 0);
	else
		ifd = vfs_open_nofollow(source, O_RDONLY);
	if (ifd < 0) {
		int save_errno = errno;
		rsyserr(FERROR_XFER, errno, "open %s", full_fname(source));
		errno = save_errno;
		return -1;
	}

	if (tmpfilefd >= 0) {
		ofd = tmpfilefd;
	} else {
		ofd = unlink_and_reopen(dest, mode, vfs_flags);
		if (ofd < 0) {
			int save_errno = errno;
			close(ifd);
			errno = save_errno;
			return -1;
		}
	}

#ifdef SUPPORT_PREALLOCATION
	if (preallocate_files) {
		STRUCT_STAT srcst;

		/* Try to preallocate enough space for file's eventual length.  Can
		 * reduce fragmentation on filesystems like ext4, xfs, and NTFS. */
		if (vfs_fstat(ifd, &srcst) < 0)
			rsyserr(FWARNING, errno, "fstat %s", full_fname(source));
		else if (srcst.st_size > 0) {
			prealloc_len = vfs_fallocate(ofd, 0, srcst.st_size);
			if (prealloc_len < 0)
				rsyserr(FWARNING, errno, "vfs_fallocate %s", full_fname(dest));
		}
	}
#endif

	while ((len = safe_read(ifd, buf, sizeof buf)) > 0) {
		if (full_write(ofd, buf, len) < 0) {
			int save_errno = errno;
			rsyserr(FERROR_XFER, errno, "write %s", full_fname(dest));
			close(ifd);
			close(ofd);
			errno = save_errno;
			return -1;
		}
		offset += len;
	}

	if (len < 0) {
		int save_errno = errno;
		rsyserr(FERROR_XFER, errno, "read %s", full_fname(source));
		close(ifd);
		close(ofd);
		errno = save_errno;
		return -1;
	}

	if (close(ifd) < 0) {
		rsyserr(FWARNING, errno, "close failed on %s",
			full_fname(source));
	}

	/* Source file might have shrunk since we fstatted it.
	 * Cut off any extra preallocated zeros from dest file. */
	if (offset < prealloc_len) {
#ifdef HAVE_FTRUNCATE
		/* If we fail to truncate, the dest file may be wrong, so we
		 * must trigger the "partial transfer" error. */
		if (vfs_ftruncate(ofd, offset) < 0)
			rsyserr(FERROR_XFER, errno, "ftruncate %s", full_fname(dest));
#else
		rprintf(FERROR_XFER, "no ftruncate for over-long pre-alloc: %s", full_fname(dest));
#endif
	}

	if (do_fsync && fsync(ofd) < 0) {
		int save_errno = errno;
		rsyserr(FERROR, errno, "fsync failed on %s", full_fname(dest));
		close(ofd);
		errno = save_errno;
		return -1;
	}

#ifdef SUPPORT_XATTRS
	/* Set xattrs through ofd while it's still held so a parent-symlink race
	 * can't redirect them onto a file outside the tree. */
	if (preserve_xattrs)
		copy_xattrs(source, dest, ofd);
#endif

	if (close(ofd) < 0) {
		int save_errno = errno;
		rsyserr(FERROR_XFER, errno, "close failed on %s", full_fname(dest));
		errno = save_errno;
		return -1;
	}

	return 0;
}

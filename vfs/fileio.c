/*
 * vfs/fileio.c - fd-based file-data ops: ftruncate, lseek, fallocate,
 * hole-punching.
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
#if defined HAVE_SYS_FALLOCATE && !defined HAVE_FALLOCATE
#include <sys/syscall.h>
#endif

#ifndef S_BLKSIZE
# if defined hpux || defined __hpux__ || defined __hpux
#  define S_BLKSIZE 1024
# elif defined _AIX && defined _I386
#  define S_BLKSIZE 4096
# else
#  define S_BLKSIZE 512
# endif
#endif

#ifdef HAVE_FTRUNCATE
int vfs_ftruncate(int fd, OFF_T size)
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



OFF_T vfs_lseek(int fd, OFF_T offset, int whence)
{
#ifdef HAVE_LSEEK64
	return lseek64(fd, offset, whence);
#else
	return lseek(fd, offset, whence);
#endif
}


#ifdef SUPPORT_PREALLOCATION
#ifdef FALLOC_FL_KEEP_SIZE
#define DO_FALLOC_OPTIONS FALLOC_FL_KEEP_SIZE
#else
#define DO_FALLOC_OPTIONS 0
#endif

OFF_T vfs_fallocate(int fd, OFF_T offset, OFF_T length)
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
	 * choose vfs_punch_hole() over a plain lseek() when turning a null run into
	 * a hole, and the receiver uses it to trim any over-preallocation.  (A
	 * stray 0 here, from 2019's switch to KEEP_SIZE, is why --preallocate
	 * --sparse stopped producing sparse files.) */
	return length;
}
#endif

/* Write all @len bytes from @ptr to @fd, retrying short writes and EINTR.
 * Returns 0 on success, -1 on error. */
static int safe_write(int fd, const char *ptr, size_t len)
{
	while (len > 0) {
		int wrote = write(fd, ptr, len);
		if (wrote <= 0) {
			if (wrote < 0 && errno == EINTR)
				continue;
			return -1;
		}
		ptr += wrote;
		len -= wrote;
	}
	return 0;
}

/* Punch a hole at pos for len bytes. The current file position must be at pos and will be
 * changed to be at pos + len. */
int vfs_punch_hole(int fd, OFF_T pos, OFF_T len)
{
#ifdef HAVE_FALLOCATE
# ifdef HAVE_FALLOC_FL_PUNCH_HOLE
	if (fallocate(fd, FALLOC_FL_PUNCH_HOLE | FALLOC_FL_KEEP_SIZE, pos, len) == 0) {
		if (vfs_lseek(fd, len, SEEK_CUR) != pos + len)
			return -1;
		return 0;
	}
# endif
# ifdef HAVE_FALLOC_FL_ZERO_RANGE
	if (fallocate(fd, FALLOC_FL_ZERO_RANGE, pos, len) == 0) {
		if (vfs_lseek(fd, len, SEEK_CUR) != pos + len)
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
			if (safe_write(fd, zeros, chunk) < 0)
				return -1;
			len -= chunk;
		}
	}
	return 0;
}

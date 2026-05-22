/*
 * Portable BSD-st_flags / Linux-chattr abstraction.
 *
 * The rsync --fileflags / --force-change family was originally written
 * for the BSD chflags(2) family.  This file provides a small wrapper
 * layer so that Linux's FS_IOC_GETFLAGS / FS_IOC_SETFLAGS ioctls
 * present the same interface to the rest of rsync, with a single
 * translation point between the two on-disk bit conventions:
 *
 *   BSD UF_NODUMP     <-> Linux FS_NODUMP_FL    (chattr +d)
 *   BSD UF_IMMUTABLE  <-> Linux FS_IMMUTABLE_FL (chattr +i)
 *   BSD SF_IMMUTABLE  -- mapped to FS_IMMUTABLE_FL on Linux
 *   BSD UF_APPEND     <-> Linux FS_APPEND_FL    (chattr +a)
 *   BSD SF_APPEND     -- mapped to FS_APPEND_FL on Linux
 *   BSD UF_NOUNLINK / SF_NOUNLINK -- no Linux equivalent (the chattr
 *     'u' flag exists in the kernel API but no mainline filesystem
 *     implements it).  Dropped on Linux receivers.
 *
 * The BSD bit layout is the on-the-wire canonical: senders fill BSD
 * bits, receivers consume BSD bits, and the translation lives at the
 * platform boundary inside this file.  That keeps interop with the
 * already-shipped BSD --fileflags rsync.
 *
 * SECURITY: all functions that touch flags operate on an open fd
 * (callers use openat(O_NOFOLLOW) or secure_relative_open first), so
 * the TOCTOU race that the path-based chflags(2) exposed is gone.
 * Symlinks aren't a meaningful target for either chattr or BSD
 * chflags semantics here, so they short-circuit to a no-op.
 */

#include "rsync.h"

#ifdef SUPPORT_FILEFLAGS

#include <sys/ioctl.h>
#ifdef HAVE_LINUX_FS_H
#include <linux/fs.h>
#endif

#ifdef HAVE_CHFLAGS
/* ---------- BSD / macOS path: native chflags(2) family ---------- */

int rsync_fchflags(int fd, UNUSED(mode_t mode), uint32 bsd_flags)
{
	return fchflags(fd, bsd_flags);
}

uint32 rsync_fgetflags(int fd, UNUSED(mode_t mode), const STRUCT_STAT *hint)
{
	STRUCT_STAT st;
	if (hint)
		return hint->st_flags;
	if (fstat(fd, &st) != 0)
		return NO_FFLAGS;
	return st.st_flags;
}

uint32 rsync_lgetflags(UNUSED(const char *path), UNUSED(mode_t mode), const STRUCT_STAT *hint)
{
	/* On BSD the value is free from lstat -- the caller already
	 * has it in the stat buffer. */
	if (hint)
		return hint->st_flags;
	{
		STRUCT_STAT st;
		if (lstat(path, &st) != 0)
			return NO_FFLAGS;
		return st.st_flags;
	}
}

#elif defined HAVE_FS_IOC_GETFLAGS
/* ---------- Linux path: FS_IOC_{GET,SET}FLAGS ioctls ---------- */

static long bsd_flags_to_linux(uint32 bf)
{
	long lf = 0;
	if (bf & UF_NODUMP)                      lf |= FS_NODUMP_FL;
	if (bf & (UF_IMMUTABLE | SF_IMMUTABLE))  lf |= FS_IMMUTABLE_FL;
	if (bf & (UF_APPEND    | SF_APPEND))     lf |= FS_APPEND_FL;
	/* UF_NOUNLINK / SF_NOUNLINK have no Linux equivalent -- dropped. */
	return lf;
}

static uint32 linux_flags_to_bsd(long lf)
{
	uint32 bf = 0;
	/* Map back into UF_* (user class).  Linux has no user/system split
	 * and most fs implementations require CAP_LINUX_IMMUTABLE for the
	 * immutable/append flags anyway, but UF_* is the safer wire choice
	 * for cross-platform interop -- a BSD receiver getting UF_* will
	 * honour SAFE_FILEFLAGS, while SF_* would require explicit
	 * --unsafe-fileflags on a non-Linux receiver. */
	if (lf & FS_NODUMP_FL)    bf |= UF_NODUMP;
	if (lf & FS_IMMUTABLE_FL) bf |= UF_IMMUTABLE;
	if (lf & FS_APPEND_FL)    bf |= UF_APPEND;
	return bf;
}

/* Filesystems that don't support the chattr ioctls return ENOTTY,
 * EOPNOTSUPP, EINVAL, or sometimes ENOSYS depending on kernel/fs.
 * Treat any of those as "no flags here" rather than as an error. */
static int errno_is_no_chattr(int e)
{
	return e == ENOTTY || e == EOPNOTSUPP || e == EINVAL
#ifdef ENOSYS
	    || e == ENOSYS
#endif
	    ;
}

/* Mask of the Linux flag bits we manage (and that map to the BSD wire).
 * Read-modify-write: we read the current ioctl flags, clear these bits,
 * then OR in our translated bits.  Bits outside this mask (FS_EXTENT_FL,
 * FS_HUGE_FILE_FL, FS_INLINE_DATA_FL, FS_INDEX_FL, FS_ENCRYPT_FL, ...) are
 * fs-internal / read-only and will be rejected by FS_IOC_SETFLAGS if we
 * try to flip them.  Preserving them is mandatory. */
#define LINUX_WIRE_MASK (FS_NODUMP_FL | FS_IMMUTABLE_FL | FS_APPEND_FL)

int rsync_fchflags(int fd, mode_t mode, uint32 bsd_flags)
{
	long cur_lf, new_lf;
	/* chattr doesn't apply to symlinks / devices / fifos / sockets. */
	if (S_ISLNK(mode) || !(S_ISREG(mode) || S_ISDIR(mode))) {
		errno = EINVAL;
		return -1;
	}
	if (ioctl(fd, FS_IOC_GETFLAGS, &cur_lf) != 0) {
		if (errno_is_no_chattr(errno) && bsd_flags == 0)
			return 0; /* nothing to do, fs doesn't support flags */
		return -1;
	}
	new_lf = (cur_lf & ~LINUX_WIRE_MASK) | bsd_flags_to_linux(bsd_flags);
	if (new_lf == cur_lf)
		return 0; /* no managed bits change */
	if (ioctl(fd, FS_IOC_SETFLAGS, &new_lf) == 0)
		return 0;
	if (errno_is_no_chattr(errno) && (new_lf & LINUX_WIRE_MASK) == (cur_lf & LINUX_WIRE_MASK))
		return 0; /* fs refuses but nothing we manage actually differs */
	return -1;
}

uint32 rsync_fgetflags(int fd, mode_t mode, UNUSED(const STRUCT_STAT *hint))
{
	long lf;
	if (S_ISLNK(mode) || !(S_ISREG(mode) || S_ISDIR(mode)))
		return 0;
	if (ioctl(fd, FS_IOC_GETFLAGS, &lf) != 0) {
		if (errno_is_no_chattr(errno))
			return 0;
		return NO_FFLAGS;
	}
	return linux_flags_to_bsd(lf);
}

uint32 rsync_lgetflags(const char *path, mode_t mode, UNUSED(const STRUCT_STAT *hint))
{
	int fd;
	uint32 bf;
	int oflags = O_RDONLY | O_NOFOLLOW;

	if (S_ISLNK(mode) || !(S_ISREG(mode) || S_ISDIR(mode)))
		return 0;
#ifdef O_NONBLOCK
	oflags |= O_NONBLOCK;
#endif
#ifdef O_DIRECTORY
	if (S_ISDIR(mode))
		oflags |= O_DIRECTORY;
#endif
	fd = open(path, oflags);
	if (fd < 0)
		return 0; /* can't open -> treat as no flags rather than failing */
	bf = rsync_fgetflags(fd, mode, NULL);
	close(fd);
	return bf == NO_FFLAGS ? 0 : bf;
}

#endif /* HAVE_CHFLAGS / HAVE_FS_IOC_GETFLAGS */

/* ---------- Cached accessor on stat_x (both platforms) ---------- */

/* Read the cached fileflags off a stat_x, populating from the lstat
 * (BSD) or via an open()+ioctl (Linux) on first access. */
uint32 stat_x_get_fileflags(stat_x *sxp, const char *path)
{
	if (sxp->fileflags_cached)
		return sxp->fileflags;
	sxp->fileflags = rsync_lgetflags(path, sxp->st.st_mode, &sxp->st);
	sxp->fileflags_cached = 1;
	return sxp->fileflags;
}

/* Invalidate the cache after a successful fchflags / chmod / chown
 * that may have changed (or freshly-applied) the inode's flags. */
void stat_x_invalidate_fileflags(stat_x *sxp)
{
	sxp->fileflags_cached = 0;
	sxp->fileflags = 0;
}

#endif /* SUPPORT_FILEFLAGS */

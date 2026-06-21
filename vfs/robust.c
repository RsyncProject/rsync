/*
 * vfs/robust.c - compound VFS ops: robust unlink/rename that retry around
 * busy files (ETXTBSY) and fall back to a cross-filesystem copy.  Layered on
 * the vfs_* unlink/rename primitives and vfs_copy_file; calls out to the
 * partial-dir handler for the EXDEV copy path.
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
#include "vfs/vfs_internal.h"

/* MAX_RENAMES should be 10**MAX_RENAMES_DIGITS */
#define MAX_RENAMES_DIGITS 3
#define MAX_RENAMES 1000

/**
 * Robust unlink: some OS'es (HPUX) refuse to unlink busy files, so
 * rename to <path>/.rsyncNNN instead.
 *
 * Note that successive rsync runs will shuffle the filenames around a
 * bit as long as the file is still busy; this is because this function
 * does not know if the unlink call is due to a new file coming in, or
 * --delete trying to remove old .rsyncNNN files, hence it renames it
 * each time.
 **/
int robust_unlink(const char *fname)
{
#ifndef ETXTBSY
	return vfs_unlink_at(fname);
#else
	static int counter = 1;
	int rc, pos, start;
	char path[MAXPATHLEN];

	rc = vfs_unlink_at(fname);
	if (rc == 0 || errno != ETXTBSY)
		return rc;

	if ((pos = strlcpy(path, fname, MAXPATHLEN)) >= MAXPATHLEN)
		pos = MAXPATHLEN - 1;

	while (pos > 0 && path[pos-1] != '/')
		pos--;
	pos += strlcpy(path+pos, ".rsync", MAXPATHLEN-pos);

	if (pos > (MAXPATHLEN-MAX_RENAMES_DIGITS-1)) {
		errno = ETXTBSY;
		return -1;
	}

	/* start where the last one left off to reduce chance of clashes */
	start = counter;
	do {
		snprintf(&path[pos], MAX_RENAMES_DIGITS+1, "%03d", counter);
		if (++counter >= MAX_RENAMES)
			counter = 1;
	} while (access(path, 0) == 0 && counter != start);

	if (INFO_GTE(MISC, 1)) {
		rprintf(FWARNING, "renaming %s to %s because of text busy\n",
			fname, path);
	}

	/* maybe we should return rename()'s exit status? Nah. */
	if (vfs_rename_at(fname, path) != 0) {
		errno = ETXTBSY;
		return -1;
	}
	return 0;
#endif
}

/* Returns 0 on successful rename, 1 if we successfully copied the file
 * across filesystems, -2 if copy_file() failed, and -1 on other errors.
 * If partialptr is not NULL and we need to do a copy, copy the file into
 * the active partial-dir instead of over the destination file. */
int robust_rename(const char *from, const char *to, const char *partialptr,
		  int mode, struct file_struct *file)
{
	int tries = 4;

	/* A resumed in-place partial-dir transfer might call us with from and
	 * to pointing to the same buf if the transfer failed yet again. */
	if (from == to)
		return 0;

	while (tries--) {
		/* tmp -> final usually live in the entry's own dir: rename via the
		 * held dir fd when both do, else the full-path wrapper. */
		int ofd = vfs_cached_dirfd(from, file);
		int nfd = vfs_cached_dirfd(to, file);
		int rr;
		if (ofd >= 0 && nfd >= 0) {
			const char *os = strrchr(from, '/');
			const char *ns = strrchr(to, '/');
			rr = vfs_rename_atfd(ofd, os ? os + 1 : from, nfd, ns ? ns + 1 : to);
		} else
			rr = vfs_rename_at(from, to);
		if (rr == 0)
			return 0;

		switch (errno) {
#ifdef ETXTBSY
		case ETXTBSY:
			if (robust_unlink(to) != 0) {
				errno = ETXTBSY;
				return -1;
			}
			errno = ETXTBSY;
			break;
#endif
		case EXDEV:
			if (partialptr) {
				if (!handle_partial_dir(partialptr,PDIR_CREATE))
					return -2;
				to = partialptr;
			}
			if (copy_file(from, to, -1, mode) != 0)
				return -2;
			vfs_unlink_at(from);
			return 1;
		default:
			return -1;
		}
	}
	return -1;
}

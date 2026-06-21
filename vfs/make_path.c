/*
 * vfs/make_path.c - compound VFS op: create a directory path, making any
 * missing parent components.  Layered on the vfs_mkdir/vfs_stat primitives.
 *
 * Moved out of util1.c as part of the VFS compound layer: filesystem mechanics
 * live in vfs/, so the operator-path resolution policy travels as an explicit
 * vfs_flags argument rather than ambient state.
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
#include "vfs/vfs_internal.h"

/* Creates a directory path, making missing components as needed.  mkp_flags is
 * MKP_DROP_NAME / MKP_SKIP_SLASH (path handling); vfs_flags is the resolution
 * policy passed straight to vfs_mkdir (VFS_OPERATOR_PATH for operator-supplied
 * --backup-dir trees, else 0).  Returns the number of dirs created, or a
 * negative value on error. */
int vfs_make_path(char *fname, int mkp_flags, int vfs_flags)
{
	char *end, *p;
	int ret = 0;

	if (mkp_flags & MKP_SKIP_SLASH) {
		while (*fname == '/')
			fname++;
	}

	while (*fname == '.' && fname[1] == '/')
		fname += 2;

	if (mkp_flags & MKP_DROP_NAME) {
		end = strrchr(fname, '/');
		if (!end || end == fname)
			return 0;
		*end = '\0';
	} else
		end = fname + strlen(fname);

	/* Try to find an existing dir, starting from the deepest dir. */
	for (p = end; ; ) {
		if (dry_run) {
			STRUCT_STAT st;
			if (vfs_stat(fname, &st) == 0) {
				if (S_ISDIR(st.st_mode))
					errno = EEXIST;
				else
					errno = ENOTDIR;
			}
		} else if (vfs_mkdir(VFS_AT_FDCWD, fname, ACCESSPERMS, vfs_flags) == 0) {
			ret++;
			break;
		}

		if (errno != ENOENT) {
			STRUCT_STAT st;
			if (errno != EEXIST || (vfs_stat(fname, &st) == 0 && !S_ISDIR(st.st_mode)))
				ret = -ret - 1;
			break;
		}
		while (1) {
			if (p == fname) {
				/* We got a relative path that doesn't exist, so assume that '.'
				 * is there and just break out and create the whole thing. */
				p = NULL;
				goto double_break;
			}
			if (*--p == '/') {
				if (p == fname) {
					/* We reached the "/" dir, which we assume is there. */
					goto double_break;
				}
				*p = '\0';
				break;
			}
		}
	}
  double_break:

	/* Make all the dirs that we didn't find on the way here. */
	while (p != end) {
		if (p)
			*p = '/';
		else
			p = fname;
		p += strlen(p);
		if (ret < 0) /* Skip mkdir on error, but keep restoring the path. */
			continue;
		if (vfs_mkdir(VFS_AT_FDCWD, fname, ACCESSPERMS, vfs_flags) < 0)
			ret = -ret - 1;
		else
			ret++;
	}

	if (mkp_flags & MKP_DROP_NAME)
		*end = '/';

	return ret;
}

/*
 * vfs/vfs.c - core state for rsync's virtual filesystem layer.
 *
 * Copyright (C) 2026 Wayne Davison, Andrew Tridgell
 *
 * This program is free software; you can redistribute it and/or modify
 * it under the terms of the GNU General Public License as published by
 * the Free Software Foundation; either version 3 of the License, or
 * (at your option) any later version.
 */

#include "rsync.h"

/* The single global VFS state.  The designated initializer makes the cache and
 * module snapshot safe by construction (a plain definition would zero base/
 * module_dirfd, making fd 0 look like a valid cached dir) -- this matters for
 * the t_*_secure test harnesses, which never run main() and so never call
 * vfs_init(). */
struct vfs vfs = {
	.dpc = { .base = -1, .anchor = (const char *)-2 },
	.module_dirfd = -1,
};

/* Reset the VFS between transfers.  Idempotent; re-establishes the same safe
 * sentinels as the static initializer.  Behaviorally inert until the held-
 * dirfd cache and module snapshot are migrated into struct vfs in later
 * commits (the live cache is still the dpc_* statics in vfs/dircache.c). */
void vfs_init(void)
{
	vfs.dpc.base = -1;
	vfs.dpc.anchor = (const char *)-2;
	vfs.dpc.depth = 0;
	vfs.module_dirfd = -1;
}

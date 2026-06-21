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

/* Reset the VFS to a safe state.  Idempotent; re-establishes the same safe
 * sentinels as the static initializer, and clears the module-root snapshot so
 * confinement state can never be stale (no module is attached yet). */
void vfs_init(void)
{
	vfs.dpc.base = -1;
	vfs.dpc.anchor = (const char *)-2;
	vfs.dpc.depth = 0;
	vfs.module_dir = NULL;
	vfs.module_dirlen = 0;
	vfs.module_dirfd = -1;
}

/* Snapshot the served daemon module root for the confinement checks.  Called
 * by clientserver.c once the module path is final (with dirfd == -1), and again
 * once the root dirfd is pinned.  The dirfd is BORROWED -- open_anchor_dirfd()
 * dup()s it; the VFS never closes it. */
void vfs_set_module_root(const char *dir, unsigned int len, int dirfd)
{
	vfs.module_dir = dir;
	vfs.module_dirlen = len;
	vfs.module_dirfd = dirfd;
}

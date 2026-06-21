/*
 * vfs/vfs.h - public interface to rsync's virtual filesystem layer.
 *
 * The VFS owns the messy, security-critical filesystem details (the do_*
 * syscall wrappers, the race-safe path resolver, the held-dirfd cache, the
 * operator-path ownership walk and the daemon module confinement) so the
 * mainline protocol/transfer code can stay clean.  State that used to be
 * scattered across syscall.c statics and clientserver.c externs lives in the
 * single global "struct vfs vfs" below.
 *
 * This header is included by rsync.h (just after proto.h) so every translation
 * unit sees the vfs_* API.  It must not include rsync.h itself.
 *
 * Copyright (C) 2026 Wayne Davison, Andrew Tridgell
 *
 * This program is free software; you can redistribute it and/or modify
 * it under the terms of the GNU General Public License as published by
 * the Free Software Foundation; either version 3 of the License, or
 * (at your option) any later version.
 */

#ifndef RSYNC_VFS_H
#define RSYNC_VFS_H

/* Max held ancestor-dirfd cache depth (was DPC_MAXDEPTH in syscall.c). */
#define VFS_DPC_MAXDEPTH 64

/* The single global VFS state instance (defined in vfs/vfs.c).
 *
 * Only curr_dir/curr_dir_len/operator_path_resolve are read by mainline code;
 * the dpc cache and the module_* snapshot are VFS-internal (touched only by
 * the vfs/ sources) and are documented as such. */
struct vfs {
	char curr_dir[MAXPATHLEN];	/* logical cwd (tracked by change_dir) */
	unsigned int curr_dir_len;

	int operator_path_resolve;	/* operator-supplied path resolver mode */

	/* VFS-INTERNAL: held ancestor-dirfd cache. */
	struct {
		const char *anchor;	/* anchor path, or sentinel (char *)-2 = none */
		int base;		/* owned anchor dir fd, or -1 */
		int fd[VFS_DPC_MAXDEPTH];	/* fd after components 0..i */
		char name[VFS_DPC_MAXDEPTH][256];	/* component names */
		int depth;
	} dpc;

	/* VFS-INTERNAL: daemon served-module-root snapshot. */
	const char *module_dir;
	unsigned int module_dirlen;
	int module_dirfd;		/* identity-pinned module root fd, or -1 */
};

extern struct vfs vfs;

/* Reset the VFS to a safe between-transfers state.  Safety at startup comes
 * from the static initializer in vfs/vfs.c, not from this call. */
void vfs_init(void);

/* Race-safe path resolution (vfs/secure_open.c). */
int vfs_relpath_active(void);
int vfs_symlink_optout_allowed(void);
int vfs_resolve_open(const char *basedir, const char *relpath, int flags, mode_t mode);
int vfs_resolve_open_at(int anchor_fd, const char *relpath, int flags, mode_t mode);

/* Operator-supplied-path resolution by ownership (vfs/owner_walk.c). */
int vfs_open_owner_walk(const char *path, int flags, mode_t mode);
int vfs_owner_walk_parent(const char *path, const char **bname);

/* Held ancestor-dirfd cache for directory traversal (vfs/dircache.c). */
int vfs_opendir(const char *dirname);
int vfs_get_dirfd(const char *dirname);
int vfs_path_dirfd(const char *anchor, const char *dirpath);
int vfs_cached_dirfd(const char *path, const struct file_struct *file);
void vfs_dircache_reset(void);

/* stat/lstat/fstat (vfs/stat.c). */
int vfs_stat(const char *path, STRUCT_STAT *st);
int vfs_lstat(const char *path, STRUCT_STAT *st);
int vfs_fstat(int fd, STRUCT_STAT *st);
int vfs_stat_at(const char *path, STRUCT_STAT *st);
int vfs_lstat_at(const char *path, STRUCT_STAT *st);
int vfs_stat_atfd(int dfd, const char *name, STRUCT_STAT *st);
int vfs_lstat_atfd(int dfd, const char *name, STRUCT_STAT *st);

/* rename (vfs/rename.c). */
int vfs_rename(const char *old_path, const char *new_path);
int vfs_rename_at(const char *old_path, const char *new_path);
int vfs_rename_atfd(int old_dfd, const char *old_name, int new_dfd, const char *new_name);

#endif /* RSYNC_VFS_H */

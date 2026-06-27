/*
 * vfs/vfs.h - public interface to rsync's virtual filesystem layer.
 *
 * The VFS owns the messy, security-critical filesystem details (the vfs_*
 * syscall wrappers, the race-safe path resolver, the held-dirfd cache, the
 * operator-path ownership walk and the daemon module confinement) so the
 * mainline protocol/transfer code can stay clean.  Implementations live in the
 * per-concern sources under vfs/ in two layers: PRIMITIVES (one file per
 * operation family, plus the resolver/dirstack/dircache/owner-walk cores) and
 * COMPOUNDS built on them (vfs_make_path, copy_file, robust_unlink/rename).  The
 * remaining VFS-internal state lives in the single "struct vfs vfs" below.
 *
 * Each single-path operation is one call taking a dirfd and a flags word:
 *
 *   vfs_<op>(int dirfd, path, ..., int flags)
 *
 *     dirfd == VFS_AT_FDCWD -- resolve `path`, per flags:
 *        0                  secure receiver resolve (race-safe O_NOFOLLOW parent
 *                           walk + *at() leaf) when vfs_relpath_active(), else plain
 *        VFS_ALLOW_SYMLINK  plain libc op -- the call site asserts the path is
 *                           trusted to follow symlinks (was the bare vfs_<op>())
 *        VFS_OPERATOR_PATH  operator-supplied path (--backup-dir/--temp-dir/
 *                           --partial-dir/--link-dest): ownership walk (follow a
 *                           uid0/euid-owned symlink, refuse a foreign one) +
 *                           daemon module-root confinement
 *     a real held dirfd -- `path` is a single component acted on directly under
 *                           it (from vfs_opendir/vfs_get_dirfd); no resolution.
 *
 * The operator-path policy is this explicit per-call flag, never ambient state.
 * VFS_REMOVEDIR turns vfs_unlink into rmdir.  chmod/lchown/symlink have no
 * ownership-walk branch (VFS_OPERATOR_PATH resolves as the default secure walk).
 * Two-path ops (vfs_rename_at, vfs_link_at) and open (distinct nofollow/
 * checklinks variants) keep explicit forms + a vfs_flags arg; vfs_fstat and the
 * fileio ops are fd-based.
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

/* dirfd sentinel for the unified vfs_* ops: "no held directory -- resolve the
 * path argument".  Always defined (even on platforms without AT_FDCWD, where the
 * held-fd forms are unavailable and report ENOSYS), so mainline code never has
 * to mention AT_FDCWD directly. */
#ifdef AT_FDCWD
#define VFS_AT_FDCWD AT_FDCWD
#else
#define VFS_AT_FDCWD (-100)
#endif

/* Per-call flags for the unified vfs_* operations.  The default (0) is the
 * secure, no-follow parent resolve used by the receiver; the flags are explicit
 * opt-ins that each carry a security meaning the call site is asserting.
 * VFS_ALLOW_SYMLINK and VFS_OPERATOR_PATH are mutually exclusive policies; if
 * both are passed VFS_ALLOW_SYMLINK wins (it is checked first).  No call site
 * needs both. */
#define VFS_ALLOW_SYMLINK  (1<<0)  /* call site is known-safe to follow symlinks */
#define VFS_OPERATOR_PATH  (1<<1)  /* operator-supplied path: ownership walk + module confinement */
#define VFS_REMOVEDIR      (1<<2)  /* unlink op targets a directory (AT_REMOVEDIR) */

/* The single global VFS state instance (defined in vfs/vfs.c).
 *
 * Only curr_dir/curr_dir_len are read by mainline code; the dpc cache and the
 * module_* snapshot are VFS-internal (touched only by the vfs/ sources) and are
 * documented as such.  The operator-supplied path resolution policy is no
 * longer ambient state -- it travels as an explicit VFS_OPERATOR_PATH flag. */
struct vfs {
	char curr_dir[MAXPATHLEN];	/* logical cwd (tracked by change_dir) */
	unsigned int curr_dir_len;

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

/* Snapshot the served daemon module root (clientserver.c calls this); the
 * dirfd is borrowed, never closed by the VFS. */
void vfs_set_module_root(const char *dir, unsigned int len, int dirfd);

/* Race-safe path resolution (vfs/secure_open.c). */
int vfs_relpath_active(void);
int vfs_symlink_optout_allowed(void);
int vfs_resolve_open(const char *basedir, const char *relpath, int flags, mode_t mode);
int vfs_resolve_open_at(int anchor_fd, const char *relpath, int flags, mode_t mode);

/* Operator-supplied-path resolution by ownership (vfs/owner_walk.c). */
int vfs_open_owner_walk(const char *path, int flags, mode_t mode, int is_operator);
int vfs_owner_walk_parent(const char *path, const char **bname, int is_operator);

/* Held ancestor-dirfd cache for directory traversal (vfs/dircache.c). */
int vfs_opendir(const char *dirname);
int vfs_get_dirfd(const char *dirname);
int vfs_path_dirfd(const char *anchor, const char *dirpath);
int vfs_cached_dirfd(const char *path, const struct file_struct *file);
void vfs_dircache_reset(void);

/* stat/lstat/fstat (vfs/stat.c). */
int vfs_stat(int dirfd, const char *path, STRUCT_STAT *st, int flags);
int vfs_lstat(int dirfd, const char *path, STRUCT_STAT *st, int flags);
int vfs_fstat(int fd, STRUCT_STAT *st);

/* rename (vfs/rename.c). */
int vfs_rename(const char *old_path, const char *new_path);
int vfs_rename_at(const char *old_path, const char *new_path, int vfs_flags);
int vfs_rename_atfd(int old_dfd, const char *old_name, int new_dfd, const char *new_name);

/* unlink and rmdir (vfs/unlink.c). */
int vfs_unlink(int dirfd, const char *path, int flags);

/* open (vfs/open.c). */
int vfs_open(const char *pathname, int flags, mode_t mode);
int vfs_open_at(const char *pathname, int flags, mode_t mode, int vfs_flags);
int vfs_open_atfd(int dfd, const char *name, int flags, mode_t mode);
int vfs_open_nofollow(const char *pathname, int flags);
int vfs_open_checklinks(const char *pathname);

/* chmod (vfs/chmod.c). */
int vfs_chmod(int dirfd, const char *path, mode_t mode, int flags);
#ifdef HAVE_CHMOD
int vfs_fchmod(int fd, mode_t mode);
#endif

/* symlink/readlink (vfs/symlink.c).  vfs_readlink is a function only in
 * fake-super builds; otherwise it is a macro -> readlink() (see rsync.h). */
int vfs_symlink(const char *lnk, int dirfd, const char *path, int flags);
ssize_t vfs_readlink_atfd(int dfd, const char *name, char *buf, size_t bufsiz);
#if defined NO_SYMLINK_XATTRS || defined NO_SYMLINK_USER_XATTRS
ssize_t vfs_readlink(const char *path, char *buf, size_t bufsiz);
#endif

/* hard links (vfs/link.c). */
int vfs_link(const char *old_path, const char *new_path);
int vfs_link_at(const char *old_path, const char *new_path, int vfs_flags);
int vfs_link_atfd(int old_dfd, const char *old_name, int new_dfd, const char *new_name, int flags);

/* mkdir / mkstemp and the trim_trailing_slashes path helper (vfs/mkdir.c). */
void trim_trailing_slashes(char *name);
/* dirfd == VFS_AT_FDCWD: resolve `path` (secure parent walk unless
 * VFS_ALLOW_SYMLINK / VFS_OPERATOR_PATH); a real held dirfd: `path` is a single
 * component created directly under it. */
int vfs_mkdir(int dirfd, char *path, mode_t mode, int flags);
int vfs_mkstemp(char *template, mode_t perms);
int vfs_mkstemp_atfd(int dfd, char *filename, mode_t perms);
int vfs_secure_mkstemp(char *template, mode_t perms, int operator_path);

/* lchown (vfs/chown.c). */
int vfs_lchown(int dirfd, const char *path, uid_t owner, gid_t group, int flags);
int vfs_fchown(int fd, uid_t owner, gid_t group);

/* device/fifo/socket node creation (vfs/mknod.c). */
int vfs_mknod(int dirfd, const char *path, mode_t mode, dev_t dev, int flags);

/* timestamp setting + crtimes (vfs/times.c). */
int vfs_setattrlist_times(const char *path, STRUCT_STAT *stp);
int vfs_setattrlist_crtime(const char *path, time_t crtime);
time_t vfs_get_create_time(const char *path, STRUCT_STAT *stp);
int vfs_SetFileTime(const char *path, time_t crtime);

/* Compound operations (vfs/<op>.c): filesystem mechanics layered on the
 * primitives above.  The resolution policy travels as an explicit vfs_flags
 * argument (VFS_OPERATOR_PATH for operator-supplied paths, else 0). */
int vfs_make_path(char *fname, int mkp_flags, int vfs_flags);
int copy_file(const char *source, const char *dest, int tmpfilefd, mode_t mode, int vfs_flags);
int robust_unlink(const char *fname, int vfs_flags);
int robust_rename(const char *from, const char *to, const char *partialptr,
		  int mode, struct file_struct *file);
int vfs_utimensat(const char *path, STRUCT_STAT *stp);
int vfs_utimensat_at(const char *path, STRUCT_STAT *stp);
int vfs_utimensat_atfd(int dfd, const char *name, STRUCT_STAT *stp);
#ifdef HAVE_FUTIMENS
int vfs_futimens(int fd, STRUCT_STAT *stp);
#endif
int vfs_lutimes(const char *path, STRUCT_STAT *stp);
int vfs_utimes(const char *path, STRUCT_STAT *stp);
int vfs_utime(const char *path, STRUCT_STAT *stp);

/* fd-based file-data ops (vfs/fileio.c). */
int vfs_ftruncate(int fd, OFF_T size);
OFF_T vfs_lseek(int fd, OFF_T offset, int whence);
OFF_T vfs_fallocate(int fd, OFF_T offset, OFF_T length);
int vfs_punch_hole(int fd, OFF_T pos, OFF_T len);

#endif /* RSYNC_VFS_H */

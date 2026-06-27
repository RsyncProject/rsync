/*
 * vfs/vfs_internal.h - private interface shared among the vfs/ sources (and,
 * during the syscall.c -> vfs/ migration, by syscall.c itself).  NOT for use by
 * mainline rsync code; the public surface is vfs/vfs.h.
 *
 * Copyright (C) 2026 Wayne Davison, Andrew Tridgell
 *
 * This program is free software; you can redistribute it and/or modify
 * it under the terms of the GNU General Public License as published by
 * the Free Software Foundation; either version 3 of the License, or
 * (at your option) any later version.
 */

#ifndef RSYNC_VFS_INTERNAL_H
#define RSYNC_VFS_INTERNAL_H

/* Option/daemon globals the VFS internals read (defined in options.c /
 * clientserver.c / syscall.c).  Centralized here so each vfs/ source picks them
 * up from one place rather than re-declaring them. */
extern int dry_run;
extern int am_root;
extern int am_sender;
extern int am_daemon;
extern int read_only;
extern int list_only;
extern int inplace;
extern int preallocate_files;
extern int sparse_files;
extern int preserve_perms;
extern int preserve_executability;
extern int open_noatime;
extern int copy_links;
extern int copy_unsafe_links;
extern int insecure_links;
extern int module_id;

/* Dry-run / read-only guard macros shared by the syscall wrappers. */
#define RETURN_ERROR_IF(x,e) \
	do { \
		if (x) { \
			errno = (e); \
			return -1; \
		} \
	} while (0)

#define RETURN_ERROR_IF_RO_OR_LO RETURN_ERROR_IF(read_only || list_only, EROFS)

/* A NULL path reaching one of the path-forwarding wrappers is always a caller
 * bug; reject it rather than forwarding NULL to libc.  Also quiets the static
 * analyzer's interprocedural nonnull false positives. */
#define RETURN_ERROR_IF_NULL(p) RETURN_ERROR_IF(!(p), EFAULT)

/* Module-confinement helpers (pure logic, always compiled). */
int path_has_dotdot_component(const char *path);
int abspath_excluded_by_module(const char *abspath, int is_operator);

#if defined(O_NOFOLLOW) && defined(O_DIRECTORY) && defined(AT_FDCWD)

#ifndef SECURE_OPEN_MAXSYMLINKS
#define SECURE_OPEN_MAXSYMLINKS 40
#endif

/* Max directory levels held open at once during a single resolve.  The walk
 * holds one fd per component, so depth is bounded by RLIMIT_NOFILE anyway; a
 * fixed array (no malloc/realloc) keeps the stack simple and the static
 * analyzer happy.  Mirrors DPC_MAXDEPTH's fixed-cap approach. */
#define DS_MAXDEPTH 1024

/* The component-walk dirfd stack used by the secure resolver: a stack of the
 * open dirfds from the anchor (index 0, borrowed) down to the current dir. */
struct dirstack {
	int fds[DS_MAXDEPTH];	/* fds[0] = anchor (borrowed); fds[top] = current dir */
	int top;
	/* Absolute path of fds[top], maintained as we descend/pop, for the
	 * exclude-aware refusal (abspath_excluded_by_module).  Empty unless the
	 * caller seeds it with the anchor's absolute path; then a followed symlink
	 * that redirects the walk into a module-excluded dir is refused. */
	char abspath[MAXPATHLEN];
};

int open_anchor_dirfd(const char *path);
int ds_init(struct dirstack *ds, int anchor);
void ds_free(struct dirstack *ds);
int ds_cur(struct dirstack *ds);
int ds_take(struct dirstack *ds);
int ds_descend(struct dirstack *ds, const char *part, int *hops);
int ds_walk_path(struct dirstack *ds, char *path, int *hops);

#endif /* O_NOFOLLOW && O_DIRECTORY && AT_FDCWD */

#endif /* RSYNC_VFS_INTERNAL_H */

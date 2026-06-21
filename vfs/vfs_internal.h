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
extern int am_daemon;
extern char *module_dir;
extern unsigned int module_dirlen;
extern int module_dirfd;

/* Module-confinement helpers (pure logic, always compiled). */
int path_has_dotdot_component(const char *path);
int abspath_excluded_by_module(const char *abspath, int name_is_dir);

#if defined(O_NOFOLLOW) && defined(O_DIRECTORY) && defined(AT_FDCWD)

#ifndef SECURE_OPEN_MAXSYMLINKS
#define SECURE_OPEN_MAXSYMLINKS 40
#endif

/* The component-walk dirfd stack used by the secure resolver: a stack of the
 * open dirfds from the anchor (index 0, borrowed) down to the current dir. */
struct dirstack {
	int *fds;	/* fds[0] = anchor (borrowed); fds[top] = current dir */
	int top;
	int cap;
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

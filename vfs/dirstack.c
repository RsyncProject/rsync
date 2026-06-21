/*
 * vfs/dirstack.c - race-safe component-walk primitives for rsync's VFS.
 *
 * The dirstack walks a relative path one component at a time, keeping an open
 * dirfd for every ancestor from the anchor down, so a parent renamed mid-walk
 * cannot redirect the climb (TOCTOU).  Plus the module-confinement helpers that
 * decide whether a resolved absolute path has escaped the served module root.
 * Moved verbatim out of syscall.c.
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

/* Returns 1 if path has any "/"-separated component that is exactly
 * "..", 0 otherwise. Used by secure_relative_open's front-door
 * validation to reject ".." inputs (bare "..", "foo/..", "subdir/..")
 * for non-re-anchored paths; the walk itself resolves an in-tree ".."
 * safely (ds_descend pops to the held parent) for a re-anchored path. */
int path_has_dotdot_component(const char *path)
{
	const char *p = path;

	while (*p) {
		const char *q;
		if (*p == '/') { p++; continue; }
		q = p;
		while (*q && *q != '/')
			q++;
		if (q - p == 2 && p[0] == '.' && p[1] == '.')
			return 1;
		p = q;
	}
	return 0;
}

/* Refuse (return 1) when the ABSOLUTE resolved path `abspath` lands OUTSIDE the
 * serving module's root, for an operator/peer-supplied path that must stay in the
 * module (--partial-dir/--backup-dir/alt-basis: operator_path_resolve).  An
 * in-tree symlink owned by uid 0 / the euid is followed by design, so it can
 * redirect the resolved target outside the module; this catches that escape.
 *
 * This is module-ROOT confinement only.  The daemon exclude/filter list is a
 * name-based visibility filter, NOT a physical-path boundary: a symlink whose own
 * name is not excluded may still resolve into an excluded IN-module subtree,
 * exactly as in stock rsync.  The defense for a writable module is `munge
 * symlinks` (see rsyncd.conf(5)), not this walk.  No-op unless we're a daemon. */
int abspath_excluded_by_module(const char *abspath, int name_is_dir)
{
	(void)name_is_dir;
	if (!am_daemon || !abspath || !module_dir)
		return 0;
	if (module_dirlen <= 1)			/* module root is "/": nothing is outside */
		return 0;
	if (strncmp(abspath, module_dir, module_dirlen) == 0
	 && (abspath[module_dirlen] == '\0' || abspath[module_dirlen] == '/'))
		return 0;			/* inside the module: name-based exclude is not a boundary */
	/* Not under the module root.  An ABSOLUTE walk passes through the module
	 * root's ancestors ("/", "/home", ...) on the way down -- those are not
	 * "outside", just not-yet-arrived, so allow them.  A path that has truly
	 * DIVERGED from the module tree is outside: refuse it for an operator/peer
	 * path that must stay in the module (operator_path_resolve); other daemon
	 * opens (--log-file, --*-from, lock/motd) may legitimately live elsewhere.
	 * The --insecure-links / "insecure links = yes" opt-out short-circuits
	 * before we get here. */
	size_t alen = strlen(abspath);
	if (alen == 0
	 || (strncmp(abspath, module_dir, alen) == 0 && module_dir[alen] == '/'))
		return 0;			/* ancestor of the module root: still descending */
	return operator_path_resolve ? 1 : 0;
}

#if defined(O_NOFOLLOW) && defined(O_DIRECTORY) && defined(AT_FDCWD)

/* Open a trusted absolute anchor directory as an owned dirfd.  When the anchor is
 * the served module root and the daemon pinned it by identity (module_dirfd), dup
 * that fd rather than re-resolving the absolute path with openat(AT_FDCWD, ...) --
 * which re-traverses the module's ancestors as the dropped-privilege module uid
 * and EACCESes when the module sits under a non-traversable parent (a 0700 home).
 * Functionally identical (same inode), just privilege-drop-safe.  Gated like its
 * callers (the secure resolver and dpc_dir_fd both require these three). */
int open_anchor_dirfd(const char *path)
{
	if (module_dirfd >= 0 && am_daemon && module_dir && strcmp(path, module_dir) == 0)
		return dup(module_dirfd);
	return openat(AT_FDCWD, path, O_RDONLY | O_DIRECTORY);
}

/* Append "/comp" to ds->abspath (no-op if it's unseeded/empty so non-daemon
 * callers pay nothing).  Returns -1 (ENAMETOOLONG) on overflow. */
static int ds_path_push(struct dirstack *ds, const char *comp)
{
	size_t al = strlen(ds->abspath);
	if (al == 0)
		return 0;		/* unseeded: tracking disabled for this walk */
	size_t cl = strlen(comp);
	if (al + 1 + cl >= sizeof ds->abspath) {
		errno = ENAMETOOLONG;
		return -1;
	}
	ds->abspath[al] = '/';
	memcpy(ds->abspath + al + 1, comp, cl + 1);
	return 0;
}

/* Drop the last component of ds->abspath (mirrors a ".." pop). */
static void ds_path_pop(struct dirstack *ds)
{
	char *slash;
	if (!ds->abspath[0])
		return;
	slash = strrchr(ds->abspath, '/');
	if (slash && slash != ds->abspath)
		*slash = '\0';
}

/* Initialise with `anchor` (which may be AT_FDCWD) as the un-owned base. */
int ds_init(struct dirstack *ds, int anchor)
{
	ds->abspath[0] = '\0';
	ds->cap = 16;
	ds->fds = (int*)malloc(ds->cap * sizeof(int));
	if (!ds->fds)
		return -1;
	ds->fds[0] = anchor;
	ds->top = 0;
	return 0;
}

/* Close every pushed fd (but not the borrowed anchor at index 0) and free. */
void ds_free(struct dirstack *ds)
{
	while (ds->top > 0)
		close(ds->fds[ds->top--]);
	free(ds->fds);
	ds->fds = NULL;
}

int ds_cur(struct dirstack *ds)
{
	return ds->fds[ds->top];
}

static int ds_push(struct dirstack *ds, int fd)
{
	if (ds->top + 1 >= ds->cap) {
		int ncap = ds->cap * 2;
		int *n = (int*)realloc(ds->fds, ncap * sizeof(int));
		if (!n) {
			close(fd);
			errno = ENOMEM;
			return -1;
		}
		ds->fds = n;
		ds->cap = ncap;
	}
	ds->fds[++ds->top] = fd;
	return 0;
}

/* Detach the current dir as an owned fd the caller must close.  At the anchor
 * (top 0) the anchor is borrowed, so return a fresh dup of it instead. */
int ds_take(struct dirstack *ds)
{
	if (ds->top > 0)
		return ds->fds[ds->top--];
	return openat(ds->fds[0], ".", O_RDONLY | O_DIRECTORY);
}

/* Descend one path component on the stack: "." stays, ".." pops to the pinned
 * parent (ELOOP at the anchor), a real subdirectory is pushed, and an in-tree
 * directory symlink is followed by walking its (relative, possibly
 * ..-containing) target on the same stack.  Returns 0, or -1 with errno set:
 * ELOOP for a refused/escaping symlink or a hop overrun, otherwise the
 * underlying openat()/readlinkat() errno (ENOENT, a real ENOTDIR, EACCES). */
int ds_descend(struct dirstack *ds, const char *part, int *hops)
{
	if (part[0] == '.' && part[1] == '\0')
		return 0;				/* "." -- no movement */
	if (part[0] == '.' && part[1] == '.' && part[2] == '\0') {
		if (ds->top == 0) {			/* would rise above the anchor */
			errno = ELOOP;
			return -1;
		}
		close(ds->fds[ds->top--]);		/* pop to the held parent fd */
		ds_path_pop(ds);
		return 0;
	}

	int fd = openat(ds_cur(ds), part, O_RDONLY | O_DIRECTORY | O_NOFOLLOW);
	if (fd != -1) {					/* a real subdirectory */
		if (ds_push(ds, fd) < 0)
			return -1;
		if (ds_path_push(ds, part) < 0)
			return -1;
		/* exclude-aware: refuse descending into a module-hidden dir (catches a
		 * symlink that redirected the walk into an excluded subtree). */
		if (abspath_excluded_by_module(ds->abspath, 1)) {
			errno = ELOOP;
			return -1;
		}
		return 0;
	}
	/* O_NOFOLLOW refused a symlink (NOFOLLOW_HIT_SYMLINK: ELOOP on Linux, EMLINK
	 * on FreeBSD, EFTYPE on NetBSD/OpenBSD), or O_DIRECTORY hit a non-directory
	 * (ENOTDIR).  Either may be a symlink, so fall through to the readlink probe;
	 * anything else is a hard error. */
	if (errno != ENOTDIR && !NOFOLLOW_HIT_SYMLINK(errno)) {
		if (errno == EMFILE || errno == ENFILE) {
			/* The resolver holds one dirfd per path component, so a deep path
			 * can exhaust descriptors where plain open() would not.  Hint at
			 * the fix once -- otherwise "Too many open files" is opaque. */
			static int warned = 0;
			if (!warned) {
				int e = errno;
				warned = 1;
				rprintf(FWARNING, "out of file descriptors resolving a deep path;"
					" raise the open-file limit (e.g. `ulimit -n`)\n");
				errno = e;
			}
		}
		return -1;
	}
	int open_errno = errno;

	char buf[MAXPATHLEN];
	ssize_t n = readlinkat(ds_cur(ds), part, buf, sizeof buf - 1);
	if (n < 0) {
		if (errno == EINVAL)			/* not a symlink: a real non-dir */
			errno = open_errno;
		return -1;
	}
	if (n == 0 || (size_t)n >= sizeof buf - 1) {
		errno = ELOOP;				/* empty or truncated target */
		return -1;
	}
	buf[n] = '\0';
	if (buf[0] == '/') {				/* absolute target: refuse */
		errno = ELOOP;
		return -1;
	}
	if (--(*hops) < 0) {
		errno = ELOOP;
		return -1;
	}
	return ds_walk_path(ds, buf, hops);
}

/* Walk every component of a relative path on the stack (used for the basedir,
 * and for a followed symlink's target -- which may contain ".."). */
int ds_walk_path(struct dirstack *ds, char *path, int *hops)
{
	char *save = NULL;
	for (char *c = strtok_r(path, "/", &save); c; c = strtok_r(NULL, "/", &save)) {
		if (ds_descend(ds, c, hops) < 0)
			return -1;
	}
	return 0;
}

#endif /* O_NOFOLLOW && O_DIRECTORY && AT_FDCWD */

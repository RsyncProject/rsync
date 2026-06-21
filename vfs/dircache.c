/*
 * vfs/dircache.c - persistent ancestor-dirfd cache for held-directory traversal.
 *
 * The file list is path-sorted, so consecutive directory resolutions share a
 * long leading prefix.  Rather than re-resolve a full path from the anchor per
 * file, we keep the whole current ancestor chain open as pinned, race-safe
 * dirfds and reuse the longest common component prefix on the next resolution.
 * vfs_opendir() hands out a held dirfd (or -1 to fall back); vfs_dircache_reset()
 * drops the chain (called by change_dir() on a real chdir).  Moved verbatim out
 * of syscall.c.
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
#include "ifuncs.h"
#include "vfs/vfs_internal.h"

/* Held-directory-fd traversal.
 *
 * Rather than re-resolve a full path on every syscall (do_*_at() re-opens the
 * parent via vfs_resolve_open() each call), the generator and receiver
 * open each directory ONCE via vfs_opendir() and issue single-component
 * *at() ops against that held dirfd with the do_*_atfd() wrappers below.  The
 * parent is a pinned fd, not re-resolved, so the per-entry symlink-race window
 * is closed and the re-resolution overhead is gone.
 *
 * vfs_opendir() owns both the authority gate and the resolver choice: it
 * returns a held dirfd only when hardened resolution is in effect, else -1
 * with errno==0 so the caller falls back to the do_*_at() wrappers
 * (behaviour-neutral).  The do_*_atfd() wrappers are thin shims with the same
 * leaf semantics as do_*_at() (dry-run/read-only guards, AT_SYMLINK_NOFOLLOW,
 * fake-super placeholder files); they never re-check the gate or re-resolve a
 * parent. */

int vfs_opendir(const char *dirname)
{
#ifdef AT_FDCWD
	int dfd;

	/* Authority gate, identical to the do_*_at() wrappers.  When hardened
	 * resolution isn't in effect, return -1 with errno cleared so the caller
	 * uses the full-path wrappers. */
	if (!vfs_relpath_active()) {
		errno = 0;
		return -1;
	}

	if (!dirname || !*dirname) {
		/* The transfer root itself (file->dirname == NULL): the cwd. */
		dfd = openat(AT_FDCWD, ".", O_RDONLY | O_DIRECTORY);
	} else if (dirname[0] == '/') {
		/* An absolute dirname is not expected for an in-transfer entry;
		 * leave it to the legacy path. */
		errno = 0;
		return -1;
	} else {
		dfd = vfs_resolve_open(NULL, dirname, O_RDONLY | O_DIRECTORY, 0);
	}

	if (dfd >= 0) {
		/* O_CLOEXEC on every tier (the per-component walk fallback
		 * doesn't thread our flags onto the returned dirfd). */
		int fl = fcntl(dfd, F_GETFD);
		if (fl >= 0)
			fcntl(dfd, F_SETFD, fl | FD_CLOEXEC);
	}
	return dfd;
#else
	(void)dirname;
	errno = 0;
	return -1;
#endif
}

/* Persistent ancestor-dirfd stack for held-directory traversal.
 *
 * The transfer's file list is path-sorted, so iterating it walks the tree in
 * DFS order and consecutive directory resolutions share a long leading prefix.
 * Rather than re-resolve a full path from the anchor each time (re-opening
 * every ancestor dir per file), we keep the whole current ancestor chain open
 * as pinned, race-safe dirfds and, on the next resolution, reuse the longest
 * common component prefix -- popping only the divergent tail and descending the
 * new tail.  Each directory is then opened once while we are inside its subtree.
 *
 * The chain is relative to the process cwd (for a NULL anchor), so change_dir()
 * drops it on any real chdir; it otherwise persists across flist chunks (the
 * pinned fds stay valid, and a raced/replaced ancestor resolves to the original
 * inode the fd holds -- the held-dirfd race-safety property, not a hazard).
 * Each component is resolved with ds_descend(), which follows in-tree directory
 * symlinks exactly as vfs_resolve_open() does; only the resolved dir fd is
 * kept (intermediate symlink-target fds are closed -- sound, since an open
 * dirfd needs no live parent). */
#if defined AT_FDCWD && defined O_NOFOLLOW && defined O_DIRECTORY

void vfs_dircache_reset(void)
{
	while (vfs.dpc.depth > 0)
		close(vfs.dpc.fd[--vfs.dpc.depth]);
	if (vfs.dpc.base >= 0)
		close(vfs.dpc.base);
	vfs.dpc.base = -1;
	vfs.dpc.anchor = (const char *)-2;
}

/* Resolve directory `dirpath` beneath `anchor` (NULL = cwd, else an absolute
 * trusted root), reusing the held ancestor stack.  Returns a BORROWED dirfd
 * owned by the cache (do NOT close), or -1 (errno preserved for a real open
 * error, errno==0 for an uncacheable path -- "..", too deep/long, or a relative
 * non-cwd anchor) so the caller can fall back to vfs_resolve_open(). */
static int dpc_dir_fd(const char *anchor, const char *dirpath)
{
	char copy[MAXPATHLEN];
	char *comps[VFS_DPC_MAXDEPTH];
	char *sv = NULL;
	int nc = 0, p, i;

	if (anchor && anchor[0] != '/') { errno = 0; return -1; }
	if (!dirpath)
		dirpath = "";
	if (dirpath[0] == '/') { errno = 0; return -1; }

	if (anchor != vfs.dpc.anchor || vfs.dpc.base < 0) {
		int fl;
		vfs_dircache_reset();
		vfs.dpc.base = open_anchor_dirfd(anchor ? anchor : ".");
		if (vfs.dpc.base < 0)
			return -1;
		if ((fl = fcntl(vfs.dpc.base, F_GETFD)) >= 0)
			fcntl(vfs.dpc.base, F_SETFD, fl | FD_CLOEXEC);
		vfs.dpc.anchor = anchor;
	}

	if (strlcpy(copy, dirpath, sizeof copy) >= sizeof copy) { errno = ENAMETOOLONG; return -1; }
	for (char *c = strtok_r(copy, "/", &sv); c; c = strtok_r(NULL, "/", &sv)) {
		if (c[0] == '.' && c[1] == '\0')
			continue;					/* "." */
		if (c[0] == '.' && c[1] == '.' && c[2] == '\0') { errno = 0; return -1; }
		if (nc >= VFS_DPC_MAXDEPTH || strlen(c) >= sizeof vfs.dpc.name[0]) {
			/* Too deep / a too-long component to cache.  Release the held
			 * ancestor fds first so the caller's full-path fallback walk does
			 * not stack on top of them: a deep tree plus a low RLIMIT_NOFILE
			 * (e.g. OpenBSD's default 128) would otherwise exhaust descriptors
			 * (cache depth + walk depth). */
			vfs_dircache_reset();
			errno = 0;
			return -1;
		}
		comps[nc++] = c;
	}

	/* Reuse the longest common prefix; drop the divergent tail. */
	for (p = 0; p < vfs.dpc.depth && p < nc && strcmp(vfs.dpc.name[p], comps[p]) == 0; p++)
		;
	while (vfs.dpc.depth > p)
		close(vfs.dpc.fd[--vfs.dpc.depth]);

	/* Descend the new tail, holding each resolved component. */
	for (i = p; i < nc; i++) {
		int afd = vfs.dpc.depth > 0 ? vfs.dpc.fd[vfs.dpc.depth-1] : vfs.dpc.base;
		struct dirstack ds;
		int hops = SECURE_OPEN_MAXSYMLINKS;
		int fd, fl;
		if (ds_init(&ds, afd) < 0)
			return -1;
		if (ds_descend(&ds, comps[i], &hops) < 0) {
			int e = errno;
			ds_free(&ds);
			errno = e;
			return -1;
		}
		fd = ds_take(&ds);
		ds_free(&ds);				/* closes intermediate symlink fds, not afd */
		if (fd < 0)
			return -1;
		if ((fl = fcntl(fd, F_GETFD)) >= 0)
			fcntl(fd, F_SETFD, fl | FD_CLOEXEC);
		strlcpy(vfs.dpc.name[vfs.dpc.depth], comps[i], sizeof vfs.dpc.name[0]);
		vfs.dpc.fd[vfs.dpc.depth++] = fd;
	}

	return nc > 0 ? vfs.dpc.fd[vfs.dpc.depth-1] : vfs.dpc.base;
}

/* Public entry for the sender (no vfs_relpath_active gate: its send paths
 * confine unconditionally).  Borrowed fd; -1 => caller uses the full walk. */
int vfs_path_dirfd(const char *anchor, const char *dirpath)
{
	return dpc_dir_fd(anchor, dirpath);
}

int vfs_get_dirfd(const char *dirname)
{
	if (!vfs_relpath_active()) { errno = 0; return -1; }
	return dpc_dir_fd(NULL, dirname);
}
#else
void vfs_dircache_reset(void)
{
}
int vfs_path_dirfd(const char *anchor, const char *dirpath)
{
	(void)anchor;
	(void)dirpath;
	errno = 0;
	return -1;
}
int vfs_get_dirfd(const char *dirname)
{
	(void)dirname;
	errno = 0;
	return -1;
}
#endif

/* Return the cached current-directory fd iff `path` lives directly in the
 * entry's own directory (file->dirname) -- the common case for held-dirfd
 * traversal.  Returns -1 (caller falls back to the do_*_at() wrappers) for
 * anything elsewhere: --temp-dir/--partial-dir/--backup-dir, an absolute path,
 * a differently-nested dir, or when vfs_opendir() is gated off.  The dirfd
 * is opened once and cached.
 *
 * file->basename is NOT assumed to equal `path`'s leaf (a temp file has a
 * different basename), so the caller derives the leaf from `path`. */
int vfs_cached_dirfd(const char *path, const struct file_struct *file)
{
	const char *slash, *dn;
	size_t plen;

	if (!path || *path == '/')
		return -1;
	dn = file && file->dirname ? file->dirname : "";
	slash = strrchr(path, '/');
	plen = slash ? (size_t)(slash - path) : 0;
	if (strlen(dn) != plen || memcmp(path, dn, plen) != 0)
		return -1;
	return vfs_get_dirfd(file ? file->dirname : NULL);
}

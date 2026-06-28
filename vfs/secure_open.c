/*
 * vfs/secure_open.c - rsync's race-safe path resolver and its policy gates.
 *
 * vfs_resolve_open()/vfs_resolve_open_at() walk a relative path one component
 * at a time beneath a trusted anchor (via the dirstack in vfs/dirstack.c), so a
 * parent-component symlink swapped mid-walk cannot redirect resolution.  The two
 * gates decide when this hardening applies and whether the symlink confinement
 * is opted out.  Moved verbatim out of syscall.c.
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

extern int am_chrooted;
extern int am_sender;
extern int module_id;
extern int insecure_links;
extern int open_noatime;

/* Single gate for whether path resolution must be hardened against
 * parent-component symlink races (TOCTOU).  Used by the do_*_at()/do_*_atfd()
 * wrappers and the receiver's secure-open/secure-mkstemp choices.  Hardens
 * every non-chrooted receiver (a chroot is its own confinement); the sender is
 * excluded so it still follows -L/--copy-links symlinks.  A daemon chroot with
 * an inner-module /./ boundary still needs these checks because the kernel
 * chroot confines the outer path, not the inner module. */
int vfs_relpath_active(void)
{
	/* The "insecure links" / --insecure-links opt-out restores the legacy
	 * follow-any-symlink behaviour uniformly, so it disables the secure
	 * resolver on the RECEIVER side too (not just the sender enumeration that
	 * already checks vfs_symlink_optout_allowed()).  Without this an opted-out
	 * module still confined receiver writes/stats through a pre-existing
	 * in-module symlink -- failing to match the pre-3.4.3 behaviour the opt-out
	 * promises (documented in rsyncd.conf(5) "munge symlinks"/"insecure links"). */
	if (vfs_symlink_optout_allowed())
		return 0;
	if (am_daemon && am_chrooted && vfs.module_dirlen)
		return 1;
	return !am_chrooted && (am_daemon || !am_sender);
}

/* Whether the operator-supplied-path symlink confinement is opted out.  For a
 * non-daemon transfer this is the local --insecure-links flag.  For a daemon it
 * is governed ONLY by the module's "insecure links" config (lp_insecure_links)
 * -- never by a peer-supplied --insecure-links (a client cannot disable a
 * daemon's confinement; the daemon also drops a connection that sends it).  So a
 * forwarded flag is structurally inert here. */
int vfs_symlink_optout_allowed(void)
{
	if (am_daemon)
		return module_id >= 0 && lp_insecure_links(module_id);
	return insecure_links;
}

/* STRICT_CONFINEMENT enforcement predicate (no effect on production behaviour).
 * True when `path` is a relative, multi-component path that the receiver-side
 * confinement is meant to protect -- i.e. a metadata op on it was expected to go
 * through a confined fd, never a raw path-based syscall.  False for: a build
 * lacking the *at/O_NOFOLLOW primitives (the portability-fallback regime), an
 * operator-supplied path (its own ownership walk governs it), an absolute path,
 * and a top-level (no-slash) path with no parent component to flip. */
int vfs_must_be_confined(const char *path, int is_operator)
{
#if defined(O_NOFOLLOW) && defined(O_DIRECTORY) && defined(AT_FDCWD)
	if (is_operator)
		return 0;
	if (!path || !*path || *path == '/')
		return 0;
	if (!strchr(path, '/'))
		return 0;
	return vfs_relpath_active();
#else
	(void)path; (void)is_operator;
	return 0;
#endif
}

#ifdef STRICT_CONFINEMENT
/* The strict build's hard stop: a confined-regime path-based metadata op was
 * about to run where a confined fd was required.  Loudly fail (the test suite
 * then catches the reintroduced fallback) rather than silently doing an
 * unconfined op a flipped parent could redirect outside the tree. */
void vfs_strict_confine_fail(const char *path, const char *what)
{
	rprintf(FERROR,
		"STRICT_CONFINEMENT violation: unconfined path-based %s on confined path \"%s\"\n",
		what ? what : "metadata op", path ? path : "(null)");
	abort();
}
#endif

/*
  open a file relative to a base directory. The basedir can be NULL,
  in which case the current working directory is used. The relpath
  must be a relative path. Resolution cannot escape basedir (or the
  cwd, when basedir is NULL): no ".." jumps above the start, no
  symlinks pointing outside, no absolute paths.

  Symlinks *within* basedir are followed normally — earlier rsync
  versions rejected every symlink with O_NOFOLLOW on each component,
  which broke legitimate directory symlinks on the receiver side
  (https://github.com/RsyncProject/rsync/issues/715).

  Escape prevention is handled by a single portable mechanism on every
  platform: a per-component O_NOFOLLOW walk on a stack of held parent
  dirfds (the dirstack helpers above). Each component is opened
  relative to a pinned parent fd, so no rename or symlink-swap can
  redirect resolution; ".." pops to the already-held parent (never
  above the anchor); an in-tree directory symlink is followed by
  reading its target and walking it on the same stack (absolute
  targets refused, symlink hops bounded). Single-component + O_NOFOLLOW
  + a pinned parent fd is race-free by construction, with no kernel
  "beneath" primitive required (see dev-notes/resolver-race-freeness).

  The relpath must also not contain any ../ elements in the path
  (except for a deliberately re-anchored module path; see below).
*/

#if defined(O_NOFOLLOW) && defined(O_DIRECTORY) && defined(AT_FDCWD)

/* Walk `relpath` confined beneath the borrowed anchor dirfd (which may be
 * AT_FDCWD) and return the opened leaf fd, or -1.  Does NOT close `anchor_fd` --
 * the caller owns it.  Shared by vfs_resolve_open() (which first resolves a
 * basedir to the anchor) and vfs_resolve_open_at() (handed an already-open
 * anchor, e.g. a held module-root fd).  `hops` is the shared symlink-hop budget. */
static int secure_walk_at(int anchor_fd, const char *anchor_abspath,
			  const char *relpath, int flags, mode_t mode, int *hops)
{
	struct dirstack ds;
	int retfd = -1;
	char *path_copy;

	if (ds_init(&ds, anchor_fd) < 0)
		return -1;
	/* Seed the abspath tracker so the exclude-aware refusal can map a resolved
	 * path back to module-relative.  Only an absolute anchor enables it. */
	if (anchor_abspath && anchor_abspath[0] == '/')
		strlcpy(ds.abspath, anchor_abspath, sizeof ds.abspath);
	path_copy = my_strdup(relpath, __FILE__, __LINE__);
	if (!path_copy) {
		ds_free(&ds);
		return -1;
	}

	/* Trim trailing slashes so the last-component test below is exact, then
	 * note the offset of the final component. */
	size_t pclen = strlen(path_copy);
	while (pclen > 1 && path_copy[pclen-1] == '/')
		path_copy[--pclen] = '\0';
	char *last_slash = strrchr(path_copy, '/');
	size_t last_off = last_slash ? (size_t)(last_slash + 1 - path_copy) : 0;

	int saw_component = 0;
	char *psave = NULL;
	for (char *part = strtok_r(path_copy, "/", &psave);
	     part != NULL;
	     part = strtok_r(NULL, "/", &psave))
	{
		int is_last = (size_t)(part - path_copy) == last_off;
		saw_component = 1;

		/* File leaf (final component, caller did not ask for O_DIRECTORY):
		 * never follow a symlink leaf. */
		if (is_last && !(flags & O_DIRECTORY)) {
			if (ds.abspath[0]) {
				char leafabs[MAXPATHLEN];
				if (snprintf(leafabs, sizeof leafabs, "%s/%s", ds.abspath, part)
				      < (int)sizeof leafabs
				 && abspath_excluded_by_module(leafabs, 0)) {
					errno = ELOOP;
					goto cleanup;
				}
			}
			int next_fd = openat(ds_cur(&ds), part, O_RDONLY | O_DIRECTORY | O_NOFOLLOW);
			if (next_fd == -1 && (errno == ENOTDIR || errno == ENOENT)) {
				retfd = openat(ds_cur(&ds), part, flags | O_NOFOLLOW, mode);
				goto cleanup;
			}
			if (next_fd == -1)
				goto cleanup;
			close(next_fd);
			errno = EISDIR;
			goto cleanup;
		}

		/* O_DIRECTORY|O_NOFOLLOW leaf: the caller's O_NOFOLLOW governs the leaf. */
		if (is_last && (flags & O_NOFOLLOW)) {
			retfd = openat(ds_cur(&ds), part, O_RDONLY | O_DIRECTORY | O_NOFOLLOW);
			goto cleanup;
		}

		/* Directory component (intermediate, or an O_DIRECTORY leaf to follow):
		 * descend on the stack, following in-tree symlinks. */
		if (ds_descend(&ds, part, hops) < 0) {
			if (!is_last && errno == ENOTDIR)
				errno = ELOOP;
			goto cleanup;
		}
		if (is_last) {
			retfd = ds_take(&ds);
			goto cleanup;
		}
	}

	/* Empty relpath: hand back a real anchor for an O_DIRECTORY caller (ds_take
	 * dups the borrowed anchor), else EISDIR.  An AT_FDCWD anchor is not a
	 * resolvable target, so it fails rather than silently returning the cwd. */
	if (!saw_component) {
		if ((flags & O_DIRECTORY) && anchor_fd != AT_FDCWD)
			retfd = ds_take(&ds);
		else
			errno = EISDIR;
	}

cleanup:
	free(path_copy);
	ds_free(&ds);
	return retfd;
}
#endif /* O_NOFOLLOW && O_DIRECTORY && AT_FDCWD */

int vfs_resolve_open(const char *basedir, const char *relpath, int flags, mode_t mode)
{
	extern int am_daemon, am_chrooted;
	char modrel_buf[MAXPATHLEN];
	int reanchored = 0;

	if (!relpath || relpath[0] == '/') {
		// must be a relative path
		errno = EINVAL;
		return -1;
	}

	/* Sanitizing daemon (am_daemon && !am_chrooted) and the /./ inner-module
	 * chroot (am_daemon && am_chrooted && vfs.module_dirlen) -- both keep the module
	 * root, not the cwd, as the trust boundary.  Here we have chdir'd into a
	 * sub-dir of the module (the transfer destination), so a relative alt-dest
	 * like "../01" may legitimately climb to a sibling that is still inside the
	 * module (#915).  Confining beneath the cwd would reject that climb.
	 * Re-anchor at the module root by prefixing the cwd's module-relative path
	 * (from rsync's logical vfs.curr_dir[], a guaranteed lexical prefix of
	 * vfs.module_dir, unlike getcwd()) and resolving beneath vfs.module_dir; RESOLVE_
	 * BENEATH then allows in-module climbs and still rejects escapes.  Only for
	 * paths that contain "..".  vfs.module_dirlen is 0 for a `path = /` module
	 * (clientserver.c), so the non-chroot arm gates on vfs.module_dir, not its
	 * length, to cover that case too -- the prefix check below treats
	 * vfs.module_dirlen 0 as "module root is /". */
	if (am_daemon && (!am_chrooted || vfs.module_dirlen)
	 && vfs.module_dir && vfs.module_dir[0] == '/'
	 && (basedir == NULL || basedir[0] != '/')
	 && (path_has_dotdot_component(relpath)
	  || (basedir && path_has_dotdot_component(basedir)))) {
		const char *p;
		int n;
		if (vfs.curr_dir_len >= vfs.module_dirlen
		 && strncmp(vfs.curr_dir, vfs.module_dir, vfs.module_dirlen) == 0
		 && (vfs.curr_dir[vfs.module_dirlen] == '\0' || vfs.curr_dir[vfs.module_dirlen] == '/')) {
			for (p = vfs.curr_dir + vfs.module_dirlen; *p == '/'; p++) {}
			if (basedir)
				n = snprintf(modrel_buf, sizeof modrel_buf, "%s%s%s/%s",
					     p, *p ? "/" : "", basedir, relpath);
			else
				n = snprintf(modrel_buf, sizeof modrel_buf, "%s%s%s",
					     p, *p ? "/" : "", relpath);
			if (n < 0 || n >= (int)sizeof modrel_buf) {
				errno = ENAMETOOLONG;
				return -1;
			}
			basedir = vfs.module_dir;	/* absolute, operator-trusted anchor */
			relpath = modrel_buf;
			reanchored = 1;
		}
		/* else: cwd not under module root as expected -- fall through to the
		 * front-door rejection below (fail safe). */
	}

	/* Reject any path with a literal ".." component (bare "..",
	 * "../foo", "foo/..", "foo/../bar", "subdir/..") at the front door,
	 * with EINVAL, so callers can rely on the validation regardless of
	 * platform.  Skipped for a re-anchored path: its ".." is deliberate,
	 * stays within the module, and is adjudicated safely by the walk
	 * below (ds_descend pops a "../" to the held parent, never above the
	 * anchor). */
	if (!reanchored) {
		if (path_has_dotdot_component(relpath)) {
			errno = EINVAL;
			return -1;
		}
		if (basedir && basedir[0] != '/' && path_has_dotdot_component(basedir)) {
			errno = EINVAL;
			return -1;
		}
	}

#ifdef O_NOATIME
	if (open_noatime)
		flags |= O_NOATIME;
#endif

#if !defined(O_NOFOLLOW) || !defined(O_DIRECTORY) || !defined(AT_FDCWD)
	// really old system, all we can do is live with the risks
	if (!basedir) {
		return open(relpath, flags, mode);
	}
	char fullpath[MAXPATHLEN];
	pathjoin(fullpath, sizeof fullpath, basedir, relpath);
	return open(fullpath, flags, mode);
#else
	int dirfd = AT_FDCWD;	/* anchor for the relpath walk (owned unless AT_FDCWD) */
	int hops = SECURE_OPEN_MAXSYMLINKS;	/* shared symlink-hop budget */
	if (basedir != NULL) {
		if (basedir[0] == '/') {
			/* Absolute basedir: operator-trusted.  Prefer the identity-pinned
			 * module-root fd when this is the served module, so a dropped-
			 * privilege daemon need not re-traverse the absolute path. */
			dirfd = open_anchor_dirfd(basedir);
			if (dirfd == -1)
				return -1;
		} else {
			/* Relative basedir: resolve it on a dirfd stack anchored at
			 * the CWD, following in-tree directory symlinks -- the
			 * portable RESOLVE_BENEATH equivalent.  A symlink target's
			 * ".." may climb but not above the CWD anchor. */
			struct dirstack bds;
			char *bcopy;
			if (ds_init(&bds, AT_FDCWD) < 0)
				return -1;
			bcopy = my_strdup(basedir, __FILE__, __LINE__);
			if (!bcopy) {
				ds_free(&bds);
				return -1;
			}
			if (ds_walk_path(&bds, bcopy, &hops) < 0) {
				int e = errno;
				free(bcopy);
				ds_free(&bds);
				errno = e;
				return -1;
			}
			free(bcopy);
			dirfd = ds_take(&bds);		/* owned dirfd for the basedir */
			ds_free(&bds);
			if (dirfd == -1)
				return -1;
		}
	}

	/* Absolute path of the anchor, for the exclude-aware refusal: the cwd (==
	 * module root for a daemon) when AT_FDCWD, or an operator-trusted absolute
	 * basedir.  A relative basedir's resolved abspath isn't tracked, so leave it
	 * unseeded (the refusal is then a no-op for that uncommon case). */
	const char *anchor_abspath = !basedir ? vfs.curr_dir
				   : (basedir[0] == '/' ? basedir : NULL);
	int retfd = secure_walk_at(dirfd, anchor_abspath, relpath, flags, mode, &hops);
	if (dirfd != AT_FDCWD)
		close(dirfd);
	return retfd;
#endif // O_NOFOLLOW, O_DIRECTORY
}

/* Like vfs_resolve_open() but anchored at an already-open directory fd
 * (borrowed -- the caller keeps ownership) rather than a basedir path.  Lets a
 * caller pin the trust root once -- e.g. a daemon's module root opened while
 * still privileged, or reached by climbing ".." up from the cwd -- and resolve a
 * relative path beneath it without re-walking (and re-permission-checking) the
 * absolute path as a dropped-privilege uid.  `relpath` must be relative and must
 * not contain a literal ".." component (a ".." inside a followed in-tree symlink
 * target is still handled by the walk). */
int vfs_resolve_open_at(int anchor_fd, const char *relpath, int flags, mode_t mode)
{
#if !defined(O_NOFOLLOW) || !defined(O_DIRECTORY) || !defined(AT_FDCWD)
	(void)anchor_fd; (void)relpath; (void)flags; (void)mode;
	errno = ENOSYS;
	return -1;
#else
	int hops = SECURE_OPEN_MAXSYMLINKS;
	if (!relpath || relpath[0] == '/') {
		errno = EINVAL;
		return -1;
	}
	if (path_has_dotdot_component(relpath)) {
		errno = EINVAL;
		return -1;
	}
#ifdef O_NOATIME
	if (open_noatime)
		flags |= O_NOATIME;
#endif
	/* The anchor fd's absolute path isn't known here (it may be a held module
	 * root or a climbed-to dir), so leave the abspath tracker unseeded; the
	 * exclude-aware refusal is a no-op for this entry point. */
	return secure_walk_at(anchor_fd, NULL, relpath, flags, mode, &hops);
#endif
}

/* Resolve ONE operand of a two-path op (rename/link) to a parent dirfd + leaf,
 * per that operand's OWN policy -- so a two-path op can confine each side
 * independently (an operator basis/backup path on one side must not relax the
 * transfer-path confinement of the other).  Policy:
 *   - side_flags & VFS_OPERATOR_PATH, or an absolute path: ownership walk
 *     (follow uid0/euid symlinks, refuse foreign; module-exclude enforced).
 *   - a relative path with a slash: secure receiver resolve of the parent.
 *   - a bare name: AT_FDCWD + the name.
 * Sets *bname and *dfd_out (a dirfd or AT_FDCWD), and *owns True when *dfd_out
 * must be closed by the caller.  dirbuf (>= MAXPATHLEN) is scratch for a parent
 * path.  Returns 0 on success, -1 (errno set) on error.  The caller must already
 * have confirmed vfs_relpath_active() (otherwise it does the plain libc op). */
int vfs_twopath_side(const char *path, int side_flags, const char **bname,
		     int *dfd_out, BOOL *owns, char *dirbuf, size_t dirbufsz)
{
	*owns = False;
#ifdef AT_FDCWD
	const char *slash = strrchr(path, '/');
	size_t dlen;

#if defined O_NOFOLLOW && defined O_DIRECTORY
	if (((side_flags & VFS_OPERATOR_PATH) || *path == '/')
	 && !vfs_symlink_optout_allowed()) {
		int dfd = vfs_owner_walk_parent(path, bname, 1);
		if (dfd < 0)
			return -1;
		*dfd_out = dfd;
		*owns = True;
		return 0;
	}
#endif
	if (*path == '/' || !slash) {
		/* absolute under --insecure-links, or a bare name: AT_FDCWD + path. */
		*bname = path;
		*dfd_out = AT_FDCWD;
		return 0;
	}
	dlen = (size_t)(slash - path);
	if (dlen >= dirbufsz) {
		errno = ENAMETOOLONG;
		return -1;
	}
	memcpy(dirbuf, path, dlen);
	dirbuf[dlen] = '\0';
	*bname = slash + 1;
	*dfd_out = vfs_resolve_open(NULL, dirbuf, O_RDONLY | O_DIRECTORY, 0);
	if (*dfd_out < 0)
		return -1;
	*owns = True;
	return 0;
#else
	(void)side_flags; (void)dirbuf; (void)dirbufsz;
	*bname = path;
	*dfd_out = -1;
	return 0;
#endif
}

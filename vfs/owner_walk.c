/*
 * vfs/owner_walk.c - operator-supplied-path resolution by ownership.
 *
 * For operator paths (--backup-dir/--temp-dir/--*-dest, daemon
 * --log-file/motd/lock/config, etc.) that may legitimately point outside the
 * transfer tree, the trust signal is authority not location: follow a symlink
 * owned by uid 0 or our euid at every component, refuse any other-uid one.
 * vfs_open_owner_walk() opens such a path; vfs_vfs_owner_walk_parent() opens its
 * parent.  Moved verbatim out of syscall.c.
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


/* Advance the tracked absolute path `abspath` by one resolved component,
 * normalizing "." and ".." exactly as openat() does so the module-confinement
 * check (abspath_excluded_by_module) sees the REAL resolved target.  -1/
 * ENAMETOOLONG on overflow. */
static int abspath_step(char *abspath, size_t cap, const char *comp, size_t comp_len)
{
	if (comp_len == 1 && comp[0] == '.')
		return 0;				/* "." -- no movement */
	if (comp_len == 2 && comp[0] == '.' && comp[1] == '.') {
		char *s = strrchr(abspath, '/');	/* ".." -- pop a component */
		if (s)
			*s = '\0';
		else
			abspath[0] = '\0';
		return 0;
	}
	size_t al = strlen(abspath);
	size_t off = (al > 0 && abspath[al-1] == '/') ? al : al + 1;	/* no "//" */
	if (off + comp_len >= cap) {
		errno = ENAMETOOLONG;
		return -1;
	}
	if (off != al)
		abspath[al] = '/';
	memcpy(abspath + off, comp, comp_len + 1);
	return 0;
}

/* Open an operator-supplied path, refusing to traverse any symlink (parent or
 * leaf) not owned by uid 0 or our euid.  A trusted-owned symlink (e.g. root's
 * /var/log -> /data/log) is still followed; an untrusted one fails ELOOP.
 * Unlike plain O_NOFOLLOW this also defends a planted parent component
 * (--log-file=$plant/log), not just a planted leaf.  Used for opens that may
 * transit attacker-writable parents: --log-file, --password-file, --*-from,
 * --read/write-batch, daemon motd/lock/early-input/--config.
 *
 * Walks component-by-component with fstatat(AT_SYMLINK_NOFOLLOW) +
 * openat(O_NOFOLLOW), splicing a trusted symlink's target back into the path.
 * Returns the fd, or -1 (errno ELOOP on the security refusal so callers can
 * tell it apart).  Falls back to plain open() where openat/O_NOFOLLOW are
 * unavailable. */
/* Core walk.  When out_abs is non-NULL and the path resolves to a directory
 * (O_DIRECTORY), the resolved absolute path is copied there -- vfs_owner_walk_parent
 * uses it to filter-check the (otherwise unchecked) leaf basename. */
static int ona_open(const char *path, int flags, mode_t mode, char *out_abs, size_t out_cap)
{
#if defined AT_FDCWD && defined O_NOFOLLOW
	/* O_CLOEXEC predates some still-supported targets; mirror rand_bytes()'s
	 * fallback in syscall.c so a build without it still compiles. */
#ifndef O_CLOEXEC
#define O_CLOEXEC 0
#endif
	if (!path || !*path) {
		errno = EINVAL;
		return -1;
	}

	/* Opted out (local --insecure-links, or a daemon module with "insecure
	 * links = yes"): restore the legacy symlink-following open. */
	if (vfs_symlink_optout_allowed())
		return open(path, flags, mode);

	const uid_t trusted_uid = geteuid();
	int dfd = AT_FDCWD;
	int dfd_owns = 0;

	/* Absolute module-relative path of the current dir, for the exclude-aware
	 * refusal (abspath_excluded_by_module).  A relative operator path starts at
	 * the daemon's cwd == the module root; an absolute one (or a followed
	 * absolute symlink target) restarts at "/". */
	char abspath[MAXPATHLEN];
	abspath[0] = '\0';
	if (am_daemon && module_dir && module_dir[0] == '/')
		strlcpy(abspath, module_dir, sizeof abspath);	/* "/" for a path=/ module */

	/* Path-walk state. `remaining` is the unconsumed tail; we splice
	 * symlink targets back into it as we go. Sized 2x MAXPATHLEN so a
	 * one-level expansion can't immediately overflow; deeper chains
	 * fail with ENAMETOOLONG below. */
	char remaining[MAXPATHLEN * 2];
	if (strlcpy(remaining, path, sizeof remaining) >= sizeof remaining) {
		errno = ENAMETOOLONG;
		return -1;
	}

	/* Absolute path: pin "/" as the starting dfd. */
	if (remaining[0] == '/') {
		dfd = open("/", O_RDONLY | O_DIRECTORY | O_CLOEXEC);
		if (dfd < 0)
			return -1;
		dfd_owns = 1;
		abspath[0] = '\0';			/* now resolving from "/" */
		char *p = remaining;
		while (*p == '/') p++;
		memmove(remaining, p, strlen(p) + 1);
	}

	int loops = 40;	/* SYMLOOP_MAX-ish; breaks symlink cycles. Counts symlink
			 * expansions only (below), NOT path depth -- a deep but
			 * symlink-free path must resolve, not ELOOP. */
	int retfd = -1;
	int saved_errno = 0;

	while (*remaining) {
		/* Peel one component off the front of `remaining`. */
		char *slash = strchr(remaining, '/');
		size_t comp_len = slash ? (size_t)(slash - remaining) : strlen(remaining);
		char comp[MAXPATHLEN];
		if (comp_len == 0 || comp_len >= sizeof comp) {
			saved_errno = comp_len == 0 ? EINVAL : ENAMETOOLONG;
			goto out;
		}
		memcpy(comp, remaining, comp_len);
		comp[comp_len] = '\0';
		int is_last = (slash == NULL);

		/* Inspect this component without following symlinks. */
		STRUCT_STAT lst;
		if (fstatat(dfd, comp, &lst, AT_SYMLINK_NOFOLLOW) < 0) {
			/* The leaf may not exist yet (O_CREAT case). Allow it
			 * and openat with O_NOFOLLOW so a race-planted leaf
			 * symlink at this instant is still refused. */
			if (is_last && errno == ENOENT && (flags & O_CREAT)) {
				if (abspath_step(abspath, sizeof abspath, comp, comp_len) < 0) {
					saved_errno = errno;
					goto out;
				}
				if (abspath_excluded_by_module(abspath, 0)) {
					saved_errno = ELOOP;
					goto out;
				}
				retfd = openat(dfd, comp, flags | O_NOFOLLOW, mode);
				saved_errno = errno;
				goto out;
			}
			saved_errno = errno;
			goto out;
		}

		if (S_ISLNK(lst.st_mode)) {
			/* Symlink: untrusted owner is refused; trusted owner
			 * is followed via readlinkat + splice. */
			if (lst.st_uid != 0 && lst.st_uid != trusted_uid) {
				saved_errno = ELOOP;
				goto out;
			}
			if (--loops < 0) {	/* cap symlink-follow chains */
				saved_errno = ELOOP;
				goto out;
			}
			char target[MAXPATHLEN];
			ssize_t n = readlinkat(dfd, comp, target, sizeof target - 1);
			if (n < 0) {
				saved_errno = errno;
				goto out;
			}
			target[n] = '\0';

			/* Splice: new `remaining` = <target> + <tail-after-comp>.
			 * Absolute target restarts the walk from "/". */
			char tail[MAXPATHLEN];
			tail[0] = '\0';
			if (slash)
				strlcpy(tail, slash, sizeof tail);

			char rebuilt[MAXPATHLEN * 2];
			if (snprintf(rebuilt, sizeof rebuilt, "%s%s",
				     target, tail) >= (int)sizeof rebuilt) {
				saved_errno = ENAMETOOLONG;
				goto out;
			}

			if (target[0] == '/') {
				if (dfd_owns) close(dfd);
				dfd = open("/", O_RDONLY | O_DIRECTORY | O_CLOEXEC);
				if (dfd < 0) {
					saved_errno = errno;
					dfd_owns = 0;
					goto out;
				}
				dfd_owns = 1;
				abspath[0] = '\0';	/* followed an absolute target: restart from "/" */
				char *p = rebuilt;
				while (*p == '/') p++;
				strlcpy(remaining, p, sizeof remaining);
			} else {
				strlcpy(remaining, rebuilt, sizeof remaining);
			}
			continue;
		}

		/* Non-symlink. */
		if (is_last) {
			if (abspath_step(abspath, sizeof abspath, comp, comp_len) < 0) {
				saved_errno = errno;
				goto out;
			}
			if (abspath_excluded_by_module(abspath, S_ISDIR(lst.st_mode))) {
				saved_errno = ELOOP;
				goto out;
			}
			retfd = openat(dfd, comp, flags | O_NOFOLLOW, mode);
			saved_errno = errno;
			/* Resolved leaf dir (O_DIRECTORY): hand its path back so
			 * vfs_owner_walk_parent can filter-check the operation's leaf. */
			if (retfd >= 0 && out_abs && out_cap)
				/* Root-resolved (".." popped abspath empty) tracked daemon walk:
				 * hand back "/" so vfs_owner_walk_parent still leaf-checks (path=/ bypass). */
				strlcpy(out_abs, (am_daemon && !abspath[0]) ? "/" : abspath, out_cap);
			goto out;
		}

		if (!S_ISDIR(lst.st_mode)) {
			saved_errno = ENOTDIR;
			goto out;
		}
		/* track the resolved path so a target outside the module is refused */
		if (abspath_step(abspath, sizeof abspath, comp, comp_len) < 0) {
			saved_errno = errno;
			goto out;
		}
		if (abspath_excluded_by_module(abspath, 1)) {
			saved_errno = ELOOP;
			goto out;
		}
		int next = openat(dfd, comp, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
		if (next < 0) {
			saved_errno = errno;
			goto out;
		}
		if (dfd_owns) close(dfd);
		dfd = next;
		dfd_owns = 1;

		/* Advance `remaining` past this component (and the slash). */
		if (slash) {
			char *p = slash;
			while (*p == '/') p++;
			memmove(remaining, p, strlen(p) + 1);
		} else {
			remaining[0] = '\0';
		}
	}

	/* Path resolved entirely to a directory (no leaf component left).
	 * If the caller wanted O_DIRECTORY we already hold the dirfd we
	 * built up; otherwise it's an EISDIR. */
	if (flags & O_DIRECTORY) {
		retfd = dfd;
		dfd_owns = 0;	/* caller now owns it */
		saved_errno = 0;
		if (out_abs && out_cap)
			/* Root-resolved (".." popped abspath empty) tracked daemon walk:
			 * hand back "/" so vfs_owner_walk_parent still leaf-checks (path=/ bypass). */
			strlcpy(out_abs, (am_daemon && !abspath[0]) ? "/" : abspath, out_cap);
	} else {
		saved_errno = EISDIR;
	}

out:
	if (dfd_owns) close(dfd);
	errno = saved_errno;
	return retfd;
#else
	/* Pre-AT_FDCWD / no O_NOFOLLOW systems: best-effort fallback. */
	(void)out_abs; (void)out_cap;
	return open(path, flags, mode);
#endif
}

int vfs_open_owner_walk(const char *path, int flags, mode_t mode)
{
	return ona_open(path, flags, mode, NULL, 0);
}

/* When set, the do_*_at() wrappers resolve their path as an OPERATOR-supplied
 * directory path (an absolute or relative --backup-dir/--temp-dir/--*-dest)
 * using the ownership walk -- follow a symlink owned by uid 0 or our euid,
 * refuse any other-uid one, at every component -- instead of the stricter
 * transfer-path resolver (which refuses all symlinks and is confined beneath the
 * transfer root).  An operator path may legitimately point outside the tree, so
 * the trust signal is authority (ownership), not location.  Set around the
 * relevant ops by backup.c et al.; the opt-out (--insecure-links / "insecure
 * links =") restores legacy following.  Default 0 (transfer-path resolver). */
int operator_path_resolve = 0;

#if defined AT_FDCWD && defined O_NOFOLLOW && defined O_DIRECTORY
/* For an operator-supplied path: open its parent directory via the ownership
 * walk (handles absolute and relative paths) and point *bname at the final
 * component.  Returns the dirfd (caller closes) or -1 with errno set. */
int vfs_owner_walk_parent(const char *path, const char **bname)
{
	const char *slash = strrchr(path, '/');
	char dir[MAXPATHLEN], pabs[MAXPATHLEN];
	size_t dlen;
	int dfd;

	*bname = slash ? slash + 1 : path;
	pabs[0] = '\0';
	if (!slash)
		dfd = ona_open(".", O_RDONLY | O_DIRECTORY, 0, pabs, sizeof pabs);
	else {
		dlen = slash == path ? 1 : (size_t)(slash - path); /* "/x" -> parent "/" */
		if (dlen >= sizeof dir) {
			errno = ENAMETOOLONG;
			return -1;
		}
		memcpy(dir, path, dlen);
		dir[dlen] = '\0';
		dfd = ona_open(dir, O_RDONLY | O_DIRECTORY, 0, pabs, sizeof pabs);
	}
	if (dfd < 0)
		return -1;
	/* owner_walk only resolved the PARENT; check the resolved leaf too, so a
	 * symlinked operator path cannot act on a leaf that resolves OUTSIDE the
	 * module in an otherwise-served dir.  (The module exclude/filter is name-
	 * based and not enforced here -- see abspath_excluded_by_module.) */
	if (pabs[0]) {
		char leafabs[MAXPATHLEN];
		STRUCT_STAT lst;
		int isdir = 0, absent = 0, refuse;
		if (fstatat(dfd, *bname, &lst, AT_SYMLINK_NOFOLLOW) == 0)
			isdir = S_ISDIR(lst.st_mode);
		else
			absent = 1;	/* mkdir/rename target: type unknown yet */
		if (snprintf(leafabs, sizeof leafabs, "%s/%s", pabs, *bname) >= (int)sizeof leafabs) {
			close(dfd);
			errno = ENAMETOOLONG;	/* fail closed, never skip the check */
			return -1;
		}
		/* For an absent leaf the op may create a dir, so also test dir-only
		 * filter rules (a "/foo/" rule never matches a file). */
		refuse = abspath_excluded_by_module(leafabs, isdir)
		      || (absent && abspath_excluded_by_module(leafabs, 1));
		if (refuse) {
			close(dfd);
			errno = ELOOP;
			return -1;
		}
	}
	return dfd;
}
#endif

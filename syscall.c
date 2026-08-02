/*
 * Syscall wrappers to ensure that nothing gets done in dry_run mode
 * and to handle system peculiarities.
 *
 * Copyright (C) 1998 Andrew Tridgell
 * Copyright (C) 2002 Martin Pool
 * Copyright (C) 2003-2022 Wayne Davison
 *
 * This program is free software; you can redistribute it and/or modify
 * it under the terms of the GNU General Public License as published by
 * the Free Software Foundation; either version 3 of the License, or
 * (at your option) any later version.
 *
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License along
 * with this program; if not, visit the http://fsf.org website.
 */

#include "rsync.h"

/* Exercise the pre-*at() portability tier on modern build hosts. */
#ifdef RSYNC_TEST_NO_AT_FDCWD
#undef AT_FDCWD
#undef AT_SYMLINK_NOFOLLOW
#undef HAVE_LINKAT
#undef HAVE_OPENAT2
#undef HAVE_UTIMENSAT
#undef O_RESOLVE_BENEATH
#endif

#if !defined MKNOD_CREATES_SOCKETS && defined HAVE_SYS_UN_H
#include <sys/un.h>	/* for the socket+bind() fallback in do_mknod() */
#endif
#ifdef HAVE_SYS_ATTR_H
#include <sys/attr.h>
#endif

#if defined HAVE_SYS_FALLOCATE && !defined HAVE_FALLOCATE
#include <sys/syscall.h>
#endif

#ifdef __linux__
#include <sys/syscall.h>	/* SYS_fchmodat2 / SYS_fallocate raw-syscall wrappers */
#endif

#include "ifuncs.h"

extern int dry_run;
extern int am_root;
extern int am_sender;
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
extern int am_daemon;
extern int am_chrooted;
extern int insecure_links;
extern int module_id;
extern unsigned int module_dirlen;
extern char *module_dir;
extern int module_dirfd;	/* daemon: served module root pinned by identity, or -1 */
extern char *confine_root;	/* --confine-root, or NULL; see confinement_root() */
extern unsigned int confine_rootlen;
extern char curr_dir[MAXPATHLEN];	/* defined below; fwd-declared for the seed */
extern int operator_path_resolve;	/* defined below; fwd-declared for the exclude check */

#if defined AT_FDCWD && defined O_NOFOLLOW && defined O_DIRECTORY
/* Open a trusted absolute anchor directory as an owned dirfd.  When the anchor is
 * the served module root and the daemon pinned it by identity (module_dirfd), dup
 * that fd rather than re-resolving the absolute path with openat(AT_FDCWD, ...) --
 * which re-traverses the module's ancestors as the dropped-privilege module uid
 * and EACCESes when the module sits under a non-traversable parent (a 0700 home).
 * Functionally identical (same inode), just privilege-drop-safe.  Gated like its
 * callers (the secure resolver and dpc_dir_fd both require these three). */
static int open_anchor_dirfd(const char *path)
{
	if (module_dirfd >= 0 && am_daemon && module_dir && strcmp(path, module_dir) == 0)
		return dup(module_dirfd);
	return openat(AT_FDCWD, path, O_RDONLY | O_DIRECTORY);
}
#endif

/* Single gate for whether path resolution must be hardened against
 * parent-component symlink races (TOCTOU).  Used by the do_*_at()/do_*_atfd()
 * wrappers and the receiver's secure-open/secure-mkstemp choices.  Hardens
 * every non-chrooted receiver (a chroot is its own confinement); the sender is
 * excluded so it still follows -L/--copy-links symlinks.  A daemon chroot with
 * an inner-module /./ boundary still needs these checks because the kernel
 * chroot confines the outer path, not the inner module. */
int secure_relpath_active(void)
{
	/* The "insecure links" / --insecure-links opt-out restores the legacy
	 * follow-any-symlink behaviour uniformly, so it disables the secure
	 * resolver on the RECEIVER side too (not just the sender enumeration that
	 * already checks symlink_optout_allowed()).  Without this an opted-out
	 * module still confined receiver writes/stats through a pre-existing
	 * in-module symlink -- failing to match the pre-3.4.3 behaviour the opt-out
	 * promises (documented in rsyncd.conf(5) "munge symlinks"/"insecure links"). */
	if (symlink_optout_allowed())
		return 0;
	if (am_daemon && am_chrooted && module_dirlen)
		return 1;
	return !am_chrooted && (am_daemon || !am_sender);
}

/* Whether the operator-supplied-path symlink confinement is opted out.  For a
 * non-daemon transfer this is the local --insecure-links flag.  For a daemon it
 * is governed ONLY by the module's "insecure links" config (lp_insecure_links)
 * -- never by a peer-supplied --insecure-links (a client cannot disable a
 * daemon's confinement; the daemon also drops a connection that sends it).  So a
 * forwarded flag is structurally inert here. */
int symlink_optout_allowed(void)
{
	if (am_daemon)
		return module_id >= 0 && lp_insecure_links(module_id);
	return insecure_links;
}

/* The root an operator/peer-supplied path must stay under, or NULL when nothing
 * is confined.  A daemon has the served module; a server launched by a wrapper
 * with its own restricted directory (rrsync) gets one from --confine-root.
 *
 * A daemon never honours --confine-root: module_dir is the boundary there, and
 * the option arrives in a peer-supplied argv, so obeying it could only loosen
 * the module. */
static const char *confinement_root(unsigned int *lenp)
{
	if (am_daemon) {
		*lenp = module_dirlen;
		return module_dir;
	}
	*lenp = confine_rootlen;
	return confine_root;
}

/* Split the "/proc/<self|pid>/fd" prefix off `p`, returning the tail -- "" for
 * the pin directory itself, otherwise a string starting with '/'.  NULL when `p`
 * is not in the fd-pin namespace at all. */
static const char *fd_pin_tail(const char *p)
{
	const char *s;

	if (strncmp(p, "/proc/", 6) != 0)
		return NULL;
	s = p + 6;
	if (strncmp(s, "self/", 5) == 0)	/* "/proc/self/..." */
		s += 4;
	else {					/* "/proc/<pid>/..." */
		const char *d = s;
		while (*s >= '0' && *s <= '9')
			s++;
		if (s == d || *s != '/')
			return NULL;
	}
	if (strncmp(s, "/fd", 3) != 0)
		return NULL;
	s += 3;
	return (*s == '\0' || *s == '/') ? s : NULL;
}

/* An EXACT pin entry, "/proc/self/fd/7" -- the one spelling whose target is what
 * confinement must judge.  rrsync also writes a pinned parent as
 * ".../fd/7/<leaf>", but the walk resolves the magic link itself and checks the
 * components past it, so only the bare entry is resolved here.  Requiring all
 * digits keeps a planted name like ".../fd/outside-secret" out. */
static int is_exact_fd_pin(const char *p)
{
	const char *tail = fd_pin_tail(p);

	if (!tail || *tail != '/')
		return 0;
	for (++tail; *tail >= '0' && *tail <= '9'; tail++) {}
	return *tail == '\0' && tail[-1] != '/';
}

/* Refuse (return 1) when the ABSOLUTE resolved path `abspath` lands OUTSIDE the
 * confinement root, for an operator/peer-supplied path that must stay inside it
 * (--partial-dir/--backup-dir/alt-basis/merge files: operator_path_resolve).  An
 * in-tree symlink owned by uid 0 / the euid is followed by design, so it can
 * redirect the resolved target outside the root; this catches that escape.
 *
 * This is ROOT confinement only.  The daemon exclude/filter list is a name-based
 * visibility filter, NOT a physical-path boundary: a symlink whose own name is
 * not excluded may still resolve into an excluded IN-tree subtree, exactly as in
 * stock rsync.  The defense for a writable module is `munge symlinks` (see
 * rsyncd.conf(5)), not this walk. */
static int abspath_outside_confinement(const char *abspath)
{
	unsigned int rootlen;
	const char *root = confinement_root(&rootlen);
	char pinned[MAXPATHLEN];

	if (!root || !abspath)
		return 0;
	if (rootlen <= 1)			/* root is "/": nothing is outside */
		return 0;
	/* An fd pin (rrsync rewrites a validated option path to /proc/self/fd/N so
	 * no later symlink can redirect it) is spelled outside the root by
	 * construction.  Judge it by what it points AT rather than by its spelling,
	 * so a pin is neither wrongly refused nor blindly trusted.  A pin we cannot
	 * resolve to an absolute path is refused, not waved through: an unreadable
	 * pin is exactly the case where we cannot say where the open would land. */
	if (!am_daemon) {
		const char *tail = fd_pin_tail(abspath);
		if (tail && !*tail)
			return 0;		/* the pin directory: transit, opens nothing */
		if (is_exact_fd_pin(abspath)) {
			ssize_t n = readlink(abspath, pinned, sizeof pinned - 1);
			if (n <= 0 || pinned[0] != '/')
				return operator_path_resolve ? 1 : 0;
			pinned[n] = '\0';
			abspath = pinned;
		}
	}
	if (strncmp(abspath, root, rootlen) == 0
	 && (abspath[rootlen] == '\0' || abspath[rootlen] == '/'))
		return 0;			/* inside: name-based exclude is not a boundary */
	/* Not under the root.  An ABSOLUTE walk passes through the root's ancestors
	 * ("/", "/home", ...) on the way down -- those are not "outside", just
	 * not-yet-arrived, so allow them.  A path that has truly DIVERGED is
	 * outside: refuse it for an operator/peer path that must stay in the tree
	 * (operator_path_resolve); other opens (--log-file, --*-from, lock/motd)
	 * may legitimately live elsewhere.  The --insecure-links / "insecure links
	 * = yes" opt-out short-circuits before we get here. */
	size_t alen = strlen(abspath);
	if (alen == 0
	 || (strncmp(abspath, root, alen) == 0 && root[alen] == '/'))
		return 0;			/* ancestor of the root: still descending */
	return operator_path_resolve ? 1 : 0;
}

/* Advance the tracked absolute path `abspath` by one resolved component,
 * normalizing "." and ".." exactly as openat() does so the module-confinement
 * check (abspath_outside_confinement) sees the REAL resolved target.  -1/
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
 * (O_DIRECTORY), the resolved absolute path is copied there -- owner_walk_parent
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
	if (symlink_optout_allowed())
		return open(path, flags, mode);

	const uid_t trusted_uid = geteuid();
	int dfd = AT_FDCWD;
	int dfd_owns = 0;

	/* Absolute path of the current dir, for the confinement refusal
	 * (abspath_outside_confinement).  A relative operator path starts at the
	 * daemon's cwd == the module root; an absolute one (or a followed absolute
	 * symlink target) restarts at "/". */
	char abspath[MAXPATHLEN];
	abspath[0] = '\0';
	if (am_daemon && module_dir && module_dir[0] == '/')
		strlcpy(abspath, module_dir, sizeof abspath);	/* "/" for a path=/ module */
	else if (confine_root) {
		/* Unlike a daemon's, this cwd is not pinned to the root -- the receiver
		 * chdir's into the destination -- so it has to be read, not assumed.
		 * It must be the PHYSICAL cwd: curr_dir is the lexical name change_dir()
		 * was given, so after descending a trusted symlink the tracker sits at a
		 * different depth than the kernel, and a ".." that really escapes looks
		 * like it landed inside.
		 *
		 * Without it there is nothing to measure against, and an empty tracker
		 * does NOT deny by itself -- a leading ".." pops nothing and an empty
		 * path reads as an ancestor of the root -- so refuse the open instead. */
		if (!getcwd(abspath, sizeof abspath))
			return -1;
	}

	/* An fd pin (rrsync rewrites an option path to /proc/self/fd/N so no
	 * later symlink can redirect it) is spelled outside the root by
	 * construction, so the walk has to be allowed through /proc/self/fd to
	 * reach the magic link.  This only suspends the check for that prefix:
	 * following the link restarts the walk at its absolute target, and every
	 * component of THAT is checked, so a pin aimed outside is still refused. */
	int pin_transit = !am_daemon && confine_root && fd_pin_tail(path) != NULL;

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
				if (!pin_transit && abspath_outside_confinement(abspath)) {
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
				/* "self" resolves to "<pid>", still inside the pin;
				 * the magic link itself lands elsewhere and ends the
				 * exemption.  Never turns back on. */
				pin_transit = pin_transit && fd_pin_tail(rebuilt) != NULL;
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
			if (!pin_transit && abspath_outside_confinement(abspath)) {
				saved_errno = ELOOP;
				goto out;
			}
			retfd = openat(dfd, comp, flags | O_NOFOLLOW, mode);
			saved_errno = errno;
			/* Resolved leaf dir (O_DIRECTORY): hand its path back so
			 * owner_walk_parent can filter-check the operation's leaf. */
			if (retfd >= 0 && out_abs && out_cap)
				/* Root-resolved (".." popped abspath empty) tracked daemon walk:
				 * hand back "/" so owner_walk_parent still leaf-checks (path=/ bypass). */
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
		if (!pin_transit && abspath_outside_confinement(abspath)) {
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
			 * hand back "/" so owner_walk_parent still leaf-checks (path=/ bypass). */
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

int open_no_attacker_symlinks(const char *path, int flags, mode_t mode)
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
int owner_walk_parent(const char *path, const char **bname)
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
	 * based and not enforced here -- see abspath_outside_confinement.) */
	if (pabs[0]) {
		char leafabs[MAXPATHLEN];
		if (snprintf(leafabs, sizeof leafabs, "%s/%s", pabs, *bname) >= (int)sizeof leafabs) {
			close(dfd);
			errno = ENAMETOOLONG;	/* fail closed, never skip the check */
			return -1;
		}
		if (abspath_outside_confinement(leafabs)) {
			close(dfd);
			errno = ELOOP;
			return -1;
		}
	}
	return dfd;
}
#endif

#ifndef S_BLKSIZE
# if defined hpux || defined __hpux__ || defined __hpux
#  define S_BLKSIZE 1024
# elif defined _AIX && defined _I386
#  define S_BLKSIZE 4096
# else
#  define S_BLKSIZE 512
# endif
#endif

#ifdef SUPPORT_CRTIMES
#ifdef HAVE_GETATTRLIST
#pragma pack(push, 4)
struct create_time {
	uint32 length;
	struct timespec crtime;
};
#pragma pack(pop)
#elif defined __CYGWIN__
#include <windows.h>
#endif
#endif

#define RETURN_ERROR_IF(x,e) \
	do { \
		if (x) { \
			errno = (e); \
			return -1; \
		} \
	} while (0)

#define RETURN_ERROR_IF_RO_OR_LO RETURN_ERROR_IF(read_only || list_only, EROFS)

/* A NULL path reaching one of the path-forwarding wrappers below is always a
 * caller bug; reject it rather than forwarding NULL to libc.  Also quiets the
 * static analyzer's interprocedural nonnull false positives. */
#define RETURN_ERROR_IF_NULL(p) RETURN_ERROR_IF(!(p), EFAULT)

int do_unlink(const char *path)
{
	if (dry_run) return 0;
	RETURN_ERROR_IF_RO_OR_LO;
	return unlink(path);
}

/*
  Symlink-race-safe variant of do_unlink() for receiver-side use. See
  the comment on do_chmod_at() for the threat model. unlink() resolves
  parent components, so a parent-symlink swap can delete an outside
  file under the daemon's authority. Defence: open the parent of path
  under secure_relative_open() and use unlinkat() (flags=0) against
  that dirfd.

  Falls through to do_unlink() for the same dry-run / non-daemon /
  chrooted / no-parent / absolute-path cases as the other wrappers.
*/
int do_unlink_at(const char *path)
{
#ifdef AT_FDCWD
	extern int am_daemon, am_chrooted;
	char dirpath[MAXPATHLEN];
	const char *bname;
	const char *slash;
	int dfd, ret, e;
	size_t dlen;

	if (dry_run) return 0;
	RETURN_ERROR_IF_RO_OR_LO;
	RETURN_ERROR_IF_NULL(path);

#if defined O_NOFOLLOW && defined O_DIRECTORY
	if (operator_path_resolve) {
		if (symlink_optout_allowed())
			return unlink(path);
		dfd = owner_walk_parent(path, &bname);
		if (dfd < 0)
			return -1;
		ret = unlinkat(dfd, bname, 0);
		e = errno;
		close(dfd);
		errno = e;
		return ret;
	}
#endif

	if (!secure_relpath_active())
		return unlink(path);

	if (!path || !*path || *path == '/')
		return unlink(path);

	slash = strrchr(path, '/');
	if (!slash)
		return unlink(path);

	dlen = slash - path;
	if (dlen >= sizeof dirpath) {
		errno = ENAMETOOLONG;
		return -1;
	}
	memcpy(dirpath, path, dlen);
	dirpath[dlen] = '\0';
	bname = slash + 1;

	dfd = secure_relative_open(NULL, dirpath, O_RDONLY | O_DIRECTORY, 0);
	if (dfd < 0)
		return -1;

	ret = unlinkat(dfd, bname, 0);
	e = errno;
	close(dfd);
	errno = e;
	return ret;
#else
	return do_unlink(path);
#endif
}

#ifdef SUPPORT_LINKS
int do_symlink(const char *lnk, const char *path)
{
	if (dry_run) return 0;
	RETURN_ERROR_IF_RO_OR_LO;
	RETURN_ERROR_IF_NULL(lnk);
	RETURN_ERROR_IF_NULL(path);

#if defined NO_SYMLINK_XATTRS || defined NO_SYMLINK_USER_XATTRS
	/* For --fake-super, we create a normal file with mode 0600
	 * and write the lnk into it. */
	if (am_root < 0) {
		int ok, len = strlen(lnk);
		int fd = open(path, O_WRONLY|O_CREAT|O_TRUNC, S_IWUSR|S_IRUSR);
		if (fd < 0)
			return -1;
		ok = write(fd, lnk, len) == len;
		if (close(fd) < 0)
			ok = 0;
		return ok ? 0 : -1;
	}
#endif

	return symlink(lnk, path);
}

/*
  Symlink-race-safe variant of do_symlink() for receiver-side use. See
  the comment on do_chmod_at() for the threat model. For a real symlink
  only the parent directory of `path` needs protection -- symlinkat()
  does not resolve the final component (it creates it). Defence: open
  the parent of `path` under secure_relative_open() and call symlinkat()
  against that dirfd; a top-level (no-slash) path has no parent to
  confine, so it uses AT_FDCWD directly. The link target string `lnk` is
  stored verbatim and not resolved at creation time, so it doesn't need
  scrutiny here.

  For --fake-super (am_root < 0) the "symlink" is written as a regular
  file, so the final component IS resolved at creation: we create it
  with openat(... O_NOFOLLOW) so a pre-planted symlink at the basename
  cannot redirect the write outside the module. This protection applies
  to top-level paths too -- the previous code fell through to the
  bare-path do_symlink() there, whose plain open() followed such a
  symlink.
*/
int do_symlink_at(const char *lnk, const char *path)
{
#ifdef AT_FDCWD
	extern int am_daemon, am_chrooted;
	char dirpath[MAXPATHLEN];
	const char *bname;
	const char *slash;
	int dfd = AT_FDCWD, ret, e;
	BOOL owns = False;
	size_t dlen;

	if (dry_run) return 0;
	RETURN_ERROR_IF_RO_OR_LO;

#if defined O_NOFOLLOW && defined O_DIRECTORY
	if (operator_path_resolve) {
		/* Operator path (e.g. an absolute --backup-dir): confine the
		 * parent with the ownership walk, then fall through to the shared
		 * leaf-creation below so fake-super emulation is preserved. */
		if (symlink_optout_allowed())
			return do_symlink(lnk, path);
		dfd = owner_walk_parent(path, &bname);
		if (dfd < 0)
			return -1;
		owns = True;
	} else
#endif
	{
		if (!secure_relpath_active())
			return do_symlink(lnk, path);

		if (!path || !*path || *path == '/')
			return do_symlink(lnk, path);

		/* A path with a slash needs secure_relative_open to confine its
		 * parent; a top-level path is in CWD (AT_FDCWD), no parent to
		 * subvert.  The leaf is protected below either way (symlinkat()
		 * won't follow it; the fake-super openat() uses O_NOFOLLOW). */
		slash = strrchr(path, '/');
		if (slash) {
			dlen = slash - path;
			if (dlen >= sizeof dirpath) {
				errno = ENAMETOOLONG;
				return -1;
			}
			memcpy(dirpath, path, dlen);
			dirpath[dlen] = '\0';
			bname = slash + 1;
			dfd = secure_relative_open(NULL, dirpath, O_RDONLY | O_DIRECTORY, 0);
			if (dfd < 0)
				return -1;
			owns = True;
		} else {
			bname = path;
		}
	}

#if defined NO_SYMLINK_XATTRS || defined NO_SYMLINK_USER_XATTRS
	/* For --fake-super, do_symlink writes the link target into a
	 * regular file rather than creating a real symlink. Do that here
	 * against the (secure or AT_FDCWD) dirfd, with O_NOFOLLOW so a pre-
	 * planted symlink at the basename can't redirect the file creation. */
	if (am_root < 0) {
		int len = strlen(lnk);
		int fd = openat(dfd, bname,
				O_WRONLY | O_CREAT | O_TRUNC | O_NOFOLLOW,
				S_IWUSR | S_IRUSR);
		if (fd < 0) {
			e = errno;
			if (owns) close(dfd);
			errno = e;
			return -1;
		}
		ret = (write(fd, lnk, len) == len) ? 0 : -1;
		if (close(fd) < 0)
			ret = -1;
		e = errno;
		if (owns) close(dfd);
		errno = e;
		return ret;
	}
#endif

	ret = symlinkat(lnk, dfd, bname);
	e = errno;
	if (owns) close(dfd);
	errno = e;
	return ret;
#else
	return do_symlink(lnk, path);
#endif
}

/* NOFOLLOW_HIT_SYMLINK() lives in rsync.h (shared with util1.c's change_dir). */

#if defined NO_SYMLINK_XATTRS || defined NO_SYMLINK_USER_XATTRS
ssize_t do_readlink(const char *path, char *buf, size_t bufsiz)
{
	/* For --fake-super, we read the link from the file. */
	if (am_root < 0) {
		int fd = do_open_nofollow(path, O_RDONLY);
		if (fd >= 0) {
			int len = read(fd, buf, bufsiz);
			close(fd);
			return len;
		}
		if (!NOFOLLOW_HIT_SYMLINK(errno))
			return -1;
		/* A real symlink needs to be turned into a fake one on the receiving
		 * side, so tell the generator that the link has no length. */
		if (!am_sender)
			return 0;
		/* Otherwise fall through and let the sender report the real length. */
	}

	return readlink(path, buf, bufsiz);
}
#endif

ssize_t do_readlink_atfd(int dfd, const char *name, char *buf, size_t bufsiz)
{
#ifdef AT_FDCWD
# if defined NO_SYMLINK_XATTRS || defined NO_SYMLINK_USER_XATTRS
	if (am_root < 0) {
		int fd = openat(dfd, name, O_RDONLY | O_NOFOLLOW);
		if (fd >= 0) {
			int len = read(fd, buf, bufsiz);
			close(fd);
			return len;
		}
		if (!NOFOLLOW_HIT_SYMLINK(errno))
			return -1;
		if (!am_sender)
			return 0;
	}
# endif
	return readlinkat(dfd, name, buf, bufsiz);
#else
	(void)dfd;
	return do_readlink(name, buf, bufsiz);
#endif
}
#endif

#if defined HAVE_LINK || defined HAVE_LINKAT
int do_link(const char *old_path, const char *new_path)
{
	if (dry_run) return 0;
	RETURN_ERROR_IF_RO_OR_LO;
	RETURN_ERROR_IF_NULL(old_path);
	RETURN_ERROR_IF_NULL(new_path);
#ifdef HAVE_LINKAT
	return linkat(AT_FDCWD, old_path, AT_FDCWD, new_path, 0);
#else
	return link(old_path, new_path);
#endif
}

/*
  Symlink-race-safe variant of do_link() for receiver-side use. See
  the comment on do_chmod_at() for the threat model. link() resolves
  parent components of *both* old_path and new_path, so a parent-
  symlink swap on either side can plant the new hard link outside
  the module, or hard-link an outside file into the module (read
  disclosure).

  Defence: open each parent under secure_relative_open() and use
  linkat() between the two dirfds, reusing one when the parents
  match. flags=0 matches the existing do_link() (don't follow a
  symbolic-link old_path). Only available on systems with linkat();
  pre-AT_FDCWD systems fall through to do_link().
*/
int do_link_at(const char *old_path, const char *new_path)
{
#if defined AT_FDCWD && defined HAVE_LINKAT
	extern int am_daemon, am_chrooted;
	char old_dirpath[MAXPATHLEN], new_dirpath[MAXPATHLEN];
	const char *old_bname, *new_bname;
	const char *old_slash, *new_slash;
	int old_dfd = AT_FDCWD, new_dfd = AT_FDCWD;
	BOOL old_owns = False, new_owns = False;
	int ret, e;
	size_t old_dlen = 0, new_dlen = 0;

	if (dry_run) return 0;
	RETURN_ERROR_IF_RO_OR_LO;

	if (!secure_relpath_active())
		return do_link(old_path, new_path);

	if (!old_path || !*old_path || !new_path || !*new_path)
		return do_link(old_path, new_path);

#if defined O_NOFOLLOW && defined O_DIRECTORY
	/* Operator-supplied path (a --backup-dir/--link-dest side): resolve each
	 * parent via the ownership walk (follow uid0/euid symlinks, refuse others). */
	if (operator_path_resolve) {
		if (symlink_optout_allowed())
			return do_link(old_path, new_path);
		old_dfd = owner_walk_parent(old_path, &old_bname);
		if (old_dfd < 0)
			return -1;
		new_dfd = owner_walk_parent(new_path, &new_bname);
		if (new_dfd < 0) {
			e = errno;
			close(old_dfd);
			errno = e;
			return -1;
		}
		ret = linkat(old_dfd, old_bname, new_dfd, new_bname, 0);
		e = errno;
		close(new_dfd);
		close(old_dfd);
		errno = e;
		return ret;
	}
#endif

	old_slash = strrchr(old_path, '/');
	new_slash = strrchr(new_path, '/');

	/* Resolve each path's parent dir independently. A path without a
	 * slash lives in CWD (AT_FDCWD), no parent open required. A path
	 * with a slash needs secure_relative_open to confine its parent
	 * resolution -- otherwise a parent symlink (e.g. --link-dest=cd
	 * where cd -> /outside) lets the kernel-level linkat(AT_FDCWD,
	 * "cd/target.txt", ...) escape the module.  An absolute path uses
	 * AT_FDCWD + the full path; each side is confined independently, so an
	 * absolute source (e.g. an absolute --link-dest) cannot disable
	 * confinement of a relative destination.  An absolute side is an operator
	 * path resolved via the ownership walk (foreign-owned parent symlink refused;
	 * --insecure-links keeps the legacy AT_FDCWD path). */
	if (*old_path == '/') {
#if defined O_NOFOLLOW && defined O_DIRECTORY
		if (!symlink_optout_allowed()) {
			operator_path_resolve = 1;	/* operator side: enforce module-exclude */
			old_dfd = owner_walk_parent(old_path, &old_bname);
			operator_path_resolve = 0;
			if (old_dfd < 0)
				return -1;
			old_owns = True;
		} else
#endif
			old_bname = old_path;
	} else if (old_slash) {
		old_dlen = old_slash - old_path;
		if (old_dlen >= sizeof old_dirpath) { errno = ENAMETOOLONG; return -1; }
		memcpy(old_dirpath, old_path, old_dlen);
		old_dirpath[old_dlen] = '\0';
		old_bname = old_slash + 1;
		old_dfd = secure_relative_open(NULL, old_dirpath, O_RDONLY | O_DIRECTORY, 0);
		if (old_dfd < 0)
			return -1;
		old_owns = True;
	} else {
		old_bname = old_path;
	}

	if (*new_path == '/') {
#if defined O_NOFOLLOW && defined O_DIRECTORY
		if (!symlink_optout_allowed()) {
			operator_path_resolve = 1;	/* operator side: enforce module-exclude */
			new_dfd = owner_walk_parent(new_path, &new_bname);
			operator_path_resolve = 0;
			if (new_dfd < 0) {
				e = errno;
				if (old_owns) close(old_dfd);
				errno = e;
				return -1;
			}
			new_owns = True;
		} else
#endif
			new_bname = new_path;
	} else if (new_slash) {
		new_dlen = new_slash - new_path;
		if (new_dlen >= sizeof new_dirpath) {
			e = ENAMETOOLONG;
			if (old_owns) close(old_dfd);
			errno = e;
			return -1;
		}
		memcpy(new_dirpath, new_path, new_dlen);
		new_dirpath[new_dlen] = '\0';
		new_bname = new_slash + 1;
		if (old_owns && old_dlen == new_dlen
		 && memcmp(old_dirpath, new_dirpath, old_dlen) == 0) {
			new_dfd = old_dfd;
		} else {
			new_dfd = secure_relative_open(NULL, new_dirpath, O_RDONLY | O_DIRECTORY, 0);
			if (new_dfd < 0) {
				e = errno;
				if (old_owns) close(old_dfd);
				errno = e;
				return -1;
			}
			new_owns = True;
		}
	} else {
		new_bname = new_path;
	}

	ret = linkat(old_dfd, old_bname, new_dfd, new_bname, 0);
	e = errno;
	if (new_owns)
		close(new_dfd);
	if (old_owns)
		close(old_dfd);
	errno = e;
	return ret;
#else
	return do_link(old_path, new_path);
#endif
}
#endif

int do_lchown(const char *path, uid_t owner, gid_t group)
{
	if (dry_run) return 0;
	RETURN_ERROR_IF_RO_OR_LO;
	RETURN_ERROR_IF_NULL(path);
#ifndef HAVE_LCHOWN
#define lchown chown
#endif
	return lchown(path, owner, group);
}

/*
  Symlink-race-safe variant of do_lchown() for receiver-side use. See the
  comment on do_chmod_at() for the threat model and design rationale.

  Resolves the parent directory under secure_relative_open() and invokes
  fchownat(..., AT_SYMLINK_NOFOLLOW) against that dirfd, so that an
  attacker who substitutes a symlink into one of the parent components
  cannot redirect the chown outside the receiver's confinement. The
  AT_SYMLINK_NOFOLLOW flag matches lchown()'s "do not follow a final-
  component symlink" semantics.

  Falls through to do_lchown() in the dry-run / non-daemon / chrooted /
  absolute-path / no-parent cases, identical to do_chmod_at().
*/
int do_lchown_at(const char *fname, uid_t owner, gid_t group)
{
#if defined AT_FDCWD && defined AT_SYMLINK_NOFOLLOW
	extern int am_daemon, am_chrooted;
	char dirpath[MAXPATHLEN];
	const char *bname;
	const char *slash;
	int dfd, ret, e;
	size_t dlen;

	if (dry_run) return 0;
	RETURN_ERROR_IF_RO_OR_LO;

#if defined O_NOFOLLOW && defined O_DIRECTORY
	/* Operator-supplied path: resolve the parent via the ownership walk, as
	 * the other do_*_at() wrappers do.  Without this the caller's
	 * operator_path_resolve has no effect here, and an absolute name would
	 * fall straight through to the unconfined full-path do_lchown(). */
	if (operator_path_resolve && fname && *fname) {
		if (symlink_optout_allowed())
			return do_lchown(fname, owner, group);
		dfd = owner_walk_parent(fname, &bname);
		if (dfd < 0)
			return -1;
		ret = fchownat(dfd, bname, owner, group, AT_SYMLINK_NOFOLLOW);
		e = errno;
		close(dfd);
		errno = e;
		return ret;
	}
#endif

	if (!secure_relpath_active())
		return do_lchown(fname, owner, group);

	if (!fname || !*fname || *fname == '/')
		return do_lchown(fname, owner, group);

	slash = strrchr(fname, '/');
	if (!slash)
		return do_lchown(fname, owner, group);

	dlen = slash - fname;
	if (dlen >= sizeof dirpath) {
		errno = ENAMETOOLONG;
		return -1;
	}
	memcpy(dirpath, fname, dlen);
	dirpath[dlen] = '\0';
	bname = slash + 1;

	dfd = secure_relative_open(NULL, dirpath, O_RDONLY | O_DIRECTORY, 0);
	if (dfd < 0)
		return -1;

	ret = fchownat(dfd, bname, owner, group, AT_SYMLINK_NOFOLLOW);
	e = errno;
	close(dfd);
	errno = e;
	return ret;
#else
	return do_lchown(fname, owner, group);
#endif
}

int do_mknod(const char *pathname, mode_t mode, dev_t dev)
{
	if (dry_run) return 0;
	RETURN_ERROR_IF_RO_OR_LO;
	RETURN_ERROR_IF_NULL(pathname);

	/* For --fake-super, we create a normal file with mode 0600. */
	if (am_root < 0) {
		int fd = open(pathname, O_WRONLY|O_CREAT|O_TRUNC, S_IWUSR|S_IRUSR);
		if (fd < 0 || close(fd) < 0)
			return -1;
		return 0;
	}

	/* Try mknod first: it handles every node type on Linux.  Only if it
	 * can't make this type on this filesystem (sockets on the BSDs/macOS/
	 * Solaris, or an old system lacking FIFO support) do we retry with the
	 * type-specific primitive.  That capability is filesystem-dependent, so
	 * it is decided per call -- not cached, not probed at build time. */
#ifdef HAVE_MKNOD
	if (mknod(pathname, mode, dev) == 0)
		return 0;
#endif
#ifdef HAVE_MKFIFO
	if (S_ISFIFO(mode))
		return mkfifo(pathname, mode);
#endif
#ifdef HAVE_SYS_UN_H
	if (S_ISSOCK(mode)) {
		int sock;
		struct sockaddr_un saddr;
		unsigned int len = strlcpy(saddr.sun_path, pathname, sizeof saddr.sun_path);
		if (len >= sizeof saddr.sun_path) {
			errno = ENAMETOOLONG;
			return -1;
		}
#ifdef HAVE_SOCKADDR_UN_LEN
		saddr.sun_len = len + 1;
#endif
		saddr.sun_family = AF_UNIX;

		if ((sock = socket(PF_UNIX, SOCK_STREAM, 0)) < 0
		 || (unlink(pathname) < 0 && errno != ENOENT)
		 || (bind(sock, (struct sockaddr*)&saddr, sizeof saddr)) < 0)
			return -1;
		close(sock);
#ifdef HAVE_CHMOD
		return do_chmod(pathname, mode);
#else
		return 0;
#endif
	}
#endif
#ifdef HAVE_MKNOD
	return -1;	/* mknod() failed for a regular/device node; errno is set */
#else
	errno = ENOSYS;
	return -1;
#endif
}

/*
  Symlink-race-safe variant of do_mknod() for receiver-side use. See
  the comment on do_chmod_at() for the threat model. Defence: open
  the parent of pathname under secure_relative_open() and use
  mknodat() against that dirfd. mknodat() covers both regular-file
  (S_IFREG with dev=0) and FIFO (S_IFIFO) and device-node creation.

  A top-level (no-slash) pathname has no parent to confine, so it uses
  AT_FDCWD; the final component is still protected (mknodat/mkfifoat do
  not follow it, and the fake-super openat() uses O_NOFOLLOW).

  Fake-super (am_root < 0) is handled inline against the (secure or
  AT_FDCWD) dirfd: it creates a regular empty file (the same file-as-
  metadata-placeholder pattern do_mknod uses) via openat() with
  O_NOFOLLOW so a pre-planted symlink at the basename can't redirect
  the file creation -- top-level paths included (the previous code fell
  through to the bare-path do_mknod() there, whose plain open() followed
  such a symlink). On Linux, sockets are recreated with mknodat() like any
  other special file; on systems where mknod() can't create sockets the
  at-variant fails instead of re-resolving an unsafe parent.
*/
int do_mknod_at(const char *pathname, mode_t mode, dev_t dev)
{
	/* HAVE_MKNODAT: older Darwin declares AT_FDCWD but not mknodat(), so
	 * the at-variant won't build there; fall back to do_mknod() (#896). */
#if defined(AT_FDCWD) && defined(HAVE_MKNODAT)
	extern int am_daemon, am_chrooted;
	char dirpath[MAXPATHLEN];
	const char *bname;
	const char *slash;
	int dfd = AT_FDCWD, ret, e;
	BOOL owns = False;
	size_t dlen;

	if (dry_run) return 0;
	RETURN_ERROR_IF_RO_OR_LO;

#if defined O_NOFOLLOW && defined O_DIRECTORY
	if (operator_path_resolve) {
		if (symlink_optout_allowed())
			return do_mknod(pathname, mode, dev);
		dfd = owner_walk_parent(pathname, &bname);
		if (dfd < 0)
			return -1;
		if (am_root < 0) {
			/* Fake-super represents a special file with an inert regular
			 * placeholder.  Keep that representation when the destination
			 * is an operator path, but create it relative to the verified
			 * parent so the confinement guarantee is unchanged. */
			int fd = openat(dfd, bname,
				O_WRONLY | O_CREAT | O_TRUNC | O_NOFOLLOW,
				S_IWUSR | S_IRUSR);
			ret = fd < 0 ? -1 : close(fd);
		} else {
			ret = mknodat(dfd, bname, mode, dev);
		}
		if (ret < 0 && am_root >= 0) {
			/* mknodat() can't make a FIFO/socket on the BSDs/macOS/
			 * Solaris (EINVAL); retry race-safely on the held dirfd,
			 * mirroring the secure-relpath path below.  Without this a
			 * FIFO backup to an operator --backup-dir fails there. */
#ifdef HAVE_MKFIFOAT
			if (S_ISFIFO(mode))
				ret = mkfifoat(dfd, bname, mode);
			else
#endif
			if (S_ISSOCK(mode))
				errno = EOPNOTSUPP; /* no dirfd-relative socket bind */
		}
		e = errno;
		close(dfd);
		errno = e;
		return ret;
	}
#endif

	if (!secure_relpath_active())
		return do_mknod(pathname, mode, dev);

	if (!pathname || !*pathname || *pathname == '/')
		return do_mknod(pathname, mode, dev);

	/* A path with a slash needs secure_relative_open to confine its
	 * parent resolution; a top-level path lives in CWD (AT_FDCWD) with
	 * no parent to subvert. The final component is protected below
	 * regardless. */
	slash = strrchr(pathname, '/');
	if (slash) {
		dlen = slash - pathname;
		if (dlen >= sizeof dirpath) {
			errno = ENAMETOOLONG;
			return -1;
		}
		memcpy(dirpath, pathname, dlen);
		dirpath[dlen] = '\0';
		bname = slash + 1;
		dfd = secure_relative_open(NULL, dirpath, O_RDONLY | O_DIRECTORY, 0);
		if (dfd < 0)
			return -1;
		owns = True;
	} else {
		bname = pathname;
	}

	if (am_root < 0) {
		/* For --fake-super, do_mknod creates a regular empty
		 * file as a placeholder for the special-file metadata
		 * (which is stored in xattrs elsewhere). Do that against
		 * the (secure or AT_FDCWD) dirfd, with O_NOFOLLOW so a
		 * pre-planted symlink at the basename can't redirect the
		 * file creation. */
		int fd = openat(dfd, bname,
				O_WRONLY | O_CREAT | O_TRUNC | O_NOFOLLOW,
				S_IWUSR | S_IRUSR);
		if (fd < 0) {
			e = errno;
			if (owns) close(dfd);
			errno = e;
			return -1;
		}
		ret = (close(fd) < 0) ? -1 : 0;
		e = errno;
		if (owns) close(dfd);
		errno = e;
		return ret;
	}

	/* Try mknodat first (handles every type on Linux); on failure retry
	 * race-safely with the type-specific primitive.  Decided per call --
	 * the capability is filesystem-dependent (see do_mknod()). */
	ret = mknodat(dfd, bname, mode, dev);
	if (ret < 0) {
#ifdef HAVE_MKFIFOAT
		if (S_ISFIFO(mode))
			ret = mkfifoat(dfd, bname, mode);
		else
#endif
		if (S_ISSOCK(mode)) {
			/* There is no dirfd-relative socket bind without
			 * /proc/self/fd: a top-level path can bind via
			 * do_mknod(), but a nested one fails safe rather than
			 * re-resolve a potentially unsafe parent. */
			if (dfd == AT_FDCWD)
				ret = do_mknod(pathname, mode, dev);
			else
				errno = EOPNOTSUPP;
		}
		/* else: regular/device node -- keep mknodat()'s errno */
	}
	e = errno;
	if (owns) close(dfd);
	errno = e;
	return ret;
#else
	return do_mknod(pathname, mode, dev);
#endif
}

int do_rmdir(const char *pathname)
{
	if (dry_run) return 0;
	RETURN_ERROR_IF_RO_OR_LO;
	return rmdir(pathname);
}

/*
  Symlink-race-safe variant of do_rmdir(). See do_unlink_at() above;
  same shape but with AT_REMOVEDIR set to require the target be a
  directory.
*/
int do_rmdir_at(const char *pathname)
{
#ifdef AT_FDCWD
	extern int am_daemon, am_chrooted;
	char dirpath[MAXPATHLEN];
	const char *bname;
	const char *slash;
	int dfd, ret, e;
	size_t dlen;

	if (dry_run) return 0;
	RETURN_ERROR_IF_RO_OR_LO;
	RETURN_ERROR_IF_NULL(pathname);

#if defined O_NOFOLLOW && defined O_DIRECTORY
	if (operator_path_resolve) {
		if (symlink_optout_allowed())
			return do_rmdir(pathname);
		dfd = owner_walk_parent(pathname, &bname);
		if (dfd < 0)
			return -1;
		ret = unlinkat(dfd, bname, AT_REMOVEDIR);
		e = errno;
		close(dfd);
		errno = e;
		return ret;
	}
#endif

	if (!secure_relpath_active())
		return rmdir(pathname);

	if (!pathname || !*pathname || *pathname == '/')
		return rmdir(pathname);

	slash = strrchr(pathname, '/');
	if (!slash)
		return rmdir(pathname);

	dlen = slash - pathname;
	if (dlen >= sizeof dirpath) {
		errno = ENAMETOOLONG;
		return -1;
	}
	memcpy(dirpath, pathname, dlen);
	dirpath[dlen] = '\0';
	bname = slash + 1;

	dfd = secure_relative_open(NULL, dirpath, O_RDONLY | O_DIRECTORY, 0);
	if (dfd < 0)
		return -1;

	ret = unlinkat(dfd, bname, AT_REMOVEDIR);
	e = errno;
	close(dfd);
	errno = e;
	return ret;
#else
	return do_rmdir(pathname);
#endif
}

int do_open(const char *pathname, int flags, mode_t mode)
{
	RETURN_ERROR_IF_NULL(pathname);
	if (flags != O_RDONLY) {
		RETURN_ERROR_IF(dry_run, 0);
		RETURN_ERROR_IF_RO_OR_LO;
	}

#ifdef O_NOATIME
	if (open_noatime)
		flags |= O_NOATIME;
#endif

	return open(pathname, flags | O_BINARY, mode);
}

/*
  Symlink-race-safe variant of do_open() for receiver-side use. See
  the comment on do_chmod_at() for the threat model. open() resolves
  parent components, so a parent-symlink swap can redirect the open
  to a file outside the module. This wrapper is defence-in-depth for
  bare-path do_open() sites that callers know are otherwise
  protected by secure parent-syscalls (e.g. generator.c's in-place
  backup creation, where robust_unlink() rejects the symlinked
  parent before this open is reached): if any of those upstream
  protections is later removed or regresses, the open here still
  refuses to escape the module.

  Defence: open the parent of pathname under secure_relative_open()
  and call openat() against the resulting dirfd with O_NOFOLLOW
  (so the basename itself isn't followed if it happens to be a
  pre-planted symlink, which is what we want for O_CREAT|O_EXCL).
*/
int do_open_at(const char *pathname, int flags, mode_t mode)
{
#ifdef AT_FDCWD
	extern int am_daemon, am_chrooted;
	char dirpath[MAXPATHLEN];
	const char *bname;
	const char *slash;
	int dfd, ret, e;
	size_t dlen;

	if (flags != O_RDONLY) {
		RETURN_ERROR_IF(dry_run, 0);
		RETURN_ERROR_IF_RO_OR_LO;
	}

#if defined O_NOFOLLOW && defined O_DIRECTORY
	if (operator_path_resolve) {
		if (symlink_optout_allowed())
			return do_open(pathname, flags, mode);
		dfd = owner_walk_parent(pathname, &bname);
		if (dfd < 0)
			return -1;
		ret = openat(dfd, bname, flags | O_NOFOLLOW, mode);
		e = errno;
		close(dfd);
		errno = e;
		return ret;
	}
#endif

	if (!secure_relpath_active())
		return do_open(pathname, flags, mode);

	if (!pathname || !*pathname || *pathname == '/')
		return do_open(pathname, flags, mode);

	slash = strrchr(pathname, '/');
	if (!slash)
		return do_open(pathname, flags, mode);

	dlen = slash - pathname;
	if (dlen >= sizeof dirpath) {
		errno = ENAMETOOLONG;
		return -1;
	}
	memcpy(dirpath, pathname, dlen);
	dirpath[dlen] = '\0';
	bname = slash + 1;

	dfd = secure_relative_open(NULL, dirpath, O_RDONLY | O_DIRECTORY, 0);
	if (dfd < 0)
		return -1;

#ifdef O_NOATIME
	if (open_noatime)
		flags |= O_NOATIME;
#endif

	ret = openat(dfd, bname, flags | O_NOFOLLOW | O_BINARY, mode);
	e = errno;
	close(dfd);
	errno = e;
	return ret;
#else
	return do_open(pathname, flags, mode);
#endif
}

#ifdef HAVE_CHMOD
int do_chmod(const char *path, mode_t mode)
{
	static int switch_step = 0;
	int code;

	if (dry_run) return 0;
	RETURN_ERROR_IF_RO_OR_LO;
	RETURN_ERROR_IF_NULL(path);

	switch (switch_step) {
#ifdef HAVE_LCHMOD
	case 0:
		if ((code = lchmod(path, mode & CHMOD_BITS)) == 0)
			break;
		if (errno == ENOSYS)
			switch_step++;
		else if (errno != ENOTSUP)
			break;
#endif
		/* FALLTHROUGH */
	default:
		if (S_ISLNK(mode)) {
# if defined HAVE_SETATTRLIST
			struct attrlist attrList;
			uint32_t m = mode & CHMOD_BITS; /* manpage is wrong: not mode_t! */

			memset(&attrList, 0, sizeof attrList);
			attrList.bitmapcount = ATTR_BIT_MAP_COUNT;
			attrList.commonattr = ATTR_CMN_ACCESSMASK;
			if ((code = setattrlist(path, &attrList, &m, sizeof m, FSOPT_NOFOLLOW)) == 0)
				break;
			if (errno == ENOTSUP)
				code = 1;
# else
			code = 1;
# endif
		} else
			code = chmod(path, mode & CHMOD_BITS); /* DISCOURAGED FUNCTION */
		break;
	}
	if (code != 0 && (preserve_perms || preserve_executability))
		return code;
	return 0;
}

/* chmod `name` relative to dfd without following a final-component symlink.
 * The held parent fd confines the ancestors; this closes the leaf race (an
 * attacker swapping the leaf to a symlink that fchmodat(...,0) would follow out
 * of the tree).
 *
 * Never follows the leaf: a regular file or dir is pinned via
 * openat(O_NOFOLLOW) and chmod'd with fchmod() (leaf-safe, every kernel, and
 * fakeroot-wrappable unlike the raw fchmodat2() syscall); a symlink leaf is
 * refused (ELOOP, or EMLINK/EFTYPE on the BSDs).  Other types or an open
 * failure fall to fchmodat(AT_SYMLINK_NOFOLLOW) (a real no-follow chmod on
 * glibc>=2.32 / Linux>=6.6), then the raw fchmodat2() syscall.  If no
 * no-follow primitive exists we skip with a warning rather than follow the
 * leaf.
 *
 * A FIFO takes the fd path on Linux and the pathname path elsewhere -- see the
 * S_ISFIFO arm below for why, and for what that costs.  Note the type used to
 * choose between them comes from the lstat above, so a leaf swapped between
 * that and the open is classified by what it WAS: an observed regular file or
 * dir that becomes a FIFO is still opened.  O_NOFOLLOW rejects symlinks, not
 * type changes.  Constraining the open to the observed type would close that;
 * it is not done here. */
static int do_fchmodat_nofollow(int dfd, const char *name, mode_t mode)
{
#if defined AT_FDCWD && defined AT_SYMLINK_NOFOLLOW
	mode &= CHMOD_BITS;
# ifdef O_NOFOLLOW
	{
		STRUCT_STAT st;
		int oflags = O_RDONLY | O_NOFOLLOW | O_NONBLOCK | O_NOCTTY;
		if (do_lstat_atfd(dfd, name, &st) < 0)
			return -1;
		if (S_ISLNK(st.st_mode)) {
			errno = ELOOP;	/* refuse to chmod through a symlink leaf */
			return -1;
		}
		if (S_ISREG(st.st_mode) || S_ISDIR(st.st_mode) || S_ISFIFO(st.st_mode)) {
			int fd;
#  ifndef __linux__
			/* Never open a FIFO here.  Opening one -- even O_NONBLOCK --
			 * makes this process a reader for as long as the descriptor
			 * lives, which wakes a writer blocked in open(O_WRONLY) and
			 * can cost it a SIGPIPE or the bytes it writes before we
			 * close.  The pathname call reaches the same end state
			 * without that: it succeeds outright when the mode is
			 * grantable, and when macOS refuses an ungrantable setgid
			 * with EPERM (having applied nothing), asking again without
			 * that bit gives exactly what fchmod() would have -- it drops
			 * the bit it cannot grant and applies the ordinary ones.
			 * Measured on macOS: fchmodat(2750) EPERM leaving 0600,
			 * fchmodat(0750) ok giving 0750, for a FIFO and a directory
			 * alike.
			 *
			 * This is a pathname call, so unlike the descriptor path it
			 * does not pin the inode; a leaf swapped for another object
			 * of the same name is chmod'd instead.  AT_SYMLINK_NOFOLLOW
			 * still keeps it off a symlink's target.  That trade buys
			 * away the reader hazard, and only for FIFOs.
			 *
			 * Only S_ISGID is retried.  An ungrantable S_ISUID would
			 * still fail where fchmod() would have cleared it, but
			 * setuid is meaningless on a FIFO and the behaviour is
			 * undemonstrated, so it is not coded for.
			 *
			 * Linux keeps the fd-first order it has always had. */
			if (S_ISFIFO(st.st_mode)) {
				if (fchmodat(dfd, name, mode, AT_SYMLINK_NOFOLLOW) == 0)
					return 0;
				if (errno == EPERM && (mode & S_ISGID)
				 && fchmodat(dfd, name, mode & ~S_ISGID,
					     AT_SYMLINK_NOFOLLOW) == 0)
					return 0;
				return -1;
			}
#  endif
#  ifdef O_CLOEXEC
			oflags |= O_CLOEXEC;
#  endif
			fd = openat(dfd, name, oflags);
			if (fd >= 0) {
				int r = fchmod(fd, mode), e = errno;
				close(fd);
				errno = e;
				return r;
			}
			/* A leaf swapped for a symlink between the lstat above and
			 * this open: refuse rather than fall through.  The errno is
			 * not the same everywhere -- Linux/Solaris ELOOP, FreeBSD
			 * EMLINK, NetBSD EFTYPE. */
			if (errno == ELOOP
#  ifdef EMLINK
			    || errno == EMLINK
#  endif
#  ifdef EFTYPE
			    || errno == EFTYPE
#  endif
			   )
				return -1;	/* raced to a symlink: refuse */
			/* otherwise (e.g. EACCES on an unreadable file) fall through */
		}
	}
# endif
# if defined __linux__
	{
		int r = fchmodat(dfd, name, mode, AT_SYMLINK_NOFOLLOW);
		if (r == 0)
			return 0;
		if (errno != ENOTSUP && errno != EOPNOTSUPP && errno != ENOSYS)
			return r;	/* a real error (EPERM, ENOENT, ...) */
	}
#  ifdef SYS_fchmodat2
	{
		int r = syscall(SYS_fchmodat2, dfd, name, (unsigned int)mode, AT_SYMLINK_NOFOLLOW);
		if (r == 0)
			return 0;
		if (errno != ENOSYS && errno != EPERM && errno != EOPNOTSUPP)
			return r;
	}
#  endif
	/* No symlink-safe chmod primitive here: skip rather than follow the leaf. */
	rprintf(FWARNING, "do_chmod: no symlink-safe chmod for \"%s\"; mode not set\n", name);
	return 1;
# else
	return fchmodat(dfd, name, mode, AT_SYMLINK_NOFOLLOW);
# endif
#else
	(void)dfd;
	(void)mode;
	/* No symlink-safe chmod primitive here: skip rather than follow the leaf. */
	rprintf(FWARNING, "do_chmod: no symlink-safe chmod for \"%s\"; mode not set\n", name);
	return 1;
#endif
}

/*
  Symlink-race-safe variant of do_chmod() for receiver-side use.

  Threat model: on a daemon running with "use chroot = no" (the prerequisite
  for CVE-2026-29518), a local attacker can race a symlink swap of one of
  the parent directory components of a path the receiver is about to chmod.
  Because chmod() resolves symlinks at every component, the swap redirects
  the chmod outside the receiver's confinement.

  Defence: open the *parent* directory of fname under secure_relative_open()
  (a portable per-component O_NOFOLLOW walk on held parent dirfds) and do
  fchmodat() against that dirfd. A symlink substituted into one of the parent
  components is then either followed within the tree (legitimate dir-symlinks
  still work) or rejected (escape attempts fail).

  Final-component handling matches do_chmod(): fchmodat() with flag 0
  follows a symlink at the final component, which is the same behaviour as
  chmod() and matches every current call site (the file being chmod'd is
  one the receiver itself just created or transferred). For the rare case
  where the caller wants to chmod a symlink-as-an-object (S_ISLNK in the
  mode bits), we fall through to do_chmod() which has portability code for
  that case.

  Falls back to do_chmod() for absolute paths and for paths with no parent
  component, where there is nothing to protect against.
*/
int do_chmod_at(const char *fname, mode_t mode)
{
#ifdef AT_FDCWD
	extern int am_daemon, am_chrooted;
	char dirpath[MAXPATHLEN];
	const char *bname;
	const char *slash;
	int dfd, ret, e;
	size_t dlen;

	if (dry_run) return 0;
	RETURN_ERROR_IF_RO_OR_LO;

#if defined O_NOFOLLOW && defined O_DIRECTORY
	/* Operator-supplied path: resolve the parent via the ownership walk, as
	 * the other do_*_at() wrappers do.  Without this the caller's
	 * operator_path_resolve has no effect here, and an absolute name would
	 * fall straight through to the unconfined full-path do_chmod().
	 * S_ISLNK(mode) still needs do_chmod()'s lchmod()/setattrlist() handling. */
	if (operator_path_resolve && fname && *fname && !S_ISLNK(mode)) {
		if (symlink_optout_allowed())
			return do_chmod(fname, mode);
		dfd = owner_walk_parent(fname, &bname);
		if (dfd < 0)
			return -1;
		ret = do_fchmodat_nofollow(dfd, bname, mode);
		e = errno;
		close(dfd);
		errno = e;
		return ret;
	}
#endif

	/* Only the daemon-without-chroot case is exposed to the symlink-
	 * race attack: a chroot already confines the receiver, and a
	 * non-daemon rsync runs with the user's own authority so a
	 * symlink they planted can only redirect to files they could
	 * already access.  Everywhere else, fall through to plain
	 * do_chmod() to avoid the dirfd-open overhead on every call. */
	if (!secure_relpath_active())
		return do_chmod(fname, mode);

	if (!fname || !*fname || *fname == '/' || S_ISLNK(mode))
		return do_chmod(fname, mode);

	slash = strrchr(fname, '/');
	if (!slash)
		return do_chmod(fname, mode);

	dlen = slash - fname;
	if (dlen >= sizeof dirpath) {
		errno = ENAMETOOLONG;
		return -1;
	}
	memcpy(dirpath, fname, dlen);
	dirpath[dlen] = '\0';
	bname = slash + 1;

	dfd = secure_relative_open(NULL, dirpath, O_RDONLY | O_DIRECTORY, 0);
	if (dfd < 0)
		return -1;

	ret = do_fchmodat_nofollow(dfd, bname, mode);
	e = errno;
	close(dfd);
	errno = e;
	return ret;
#else
	return do_chmod(fname, mode);
#endif
}
#endif

int do_rename(const char *old_path, const char *new_path)
{
	if (dry_run) return 0;
	RETURN_ERROR_IF_RO_OR_LO;
	return rename(old_path, new_path);
}

/*
  Symlink-race-safe variant of do_rename() for receiver-side use. See
  the comment on do_chmod_at() for the threat model and design rationale.

  rename() is the central tmp -> final operation in rsync; if either the
  source or the destination has an attacker-substituted symlink in one
  of its parent components, the rename can publish or vanish files
  outside the module. Defence: open the parent of *each* path under
  secure_relative_open() and use renameat() against the resulting
  dirfds. When old_path and new_path share the same parent (the common
  case -- tmp file living next to its final name), we reuse the same
  dirfd for both sides.

  Falls through to do_rename() in dry-run, non-daemon, chrooted and
  absolute-path cases, identical to the other do_*_at() wrappers.
*/
int do_rename_at(const char *old_path, const char *new_path)
{
#ifdef AT_FDCWD
	extern int am_daemon, am_chrooted;
	char old_dirpath[MAXPATHLEN], new_dirpath[MAXPATHLEN];
	const char *old_bname, *new_bname;
	const char *old_slash, *new_slash;
	int old_dfd = AT_FDCWD, new_dfd = AT_FDCWD;
	BOOL old_owns = False, new_owns = False;
	int ret = -1, e;
	size_t old_dlen = 0, new_dlen = 0;

	if (dry_run) return 0;
	RETURN_ERROR_IF_RO_OR_LO;

	if (!secure_relpath_active())
		return do_rename(old_path, new_path);

	if (!old_path || !*old_path || !new_path || !*new_path)
		return do_rename(old_path, new_path);

#if defined O_NOFOLLOW && defined O_DIRECTORY
	/* Operator-supplied path (e.g. a --backup-dir destination or a --temp-dir
	 * source): resolve each side's parent via the ownership walk (follow
	 * uid0/euid symlinks, refuse others; absolute and relative alike). */
	if (operator_path_resolve) {
		if (symlink_optout_allowed())
			return do_rename(old_path, new_path);
		old_dfd = owner_walk_parent(old_path, &old_bname);
		if (old_dfd < 0)
			return -1;
		new_dfd = owner_walk_parent(new_path, &new_bname);
		if (new_dfd < 0) {
			e = errno;
			close(old_dfd);
			errno = e;
			return -1;
		}
		ret = renameat(old_dfd, old_bname, new_dfd, new_bname);
		e = errno;
		close(new_dfd);
		close(old_dfd);
		errno = e;
		return ret;
	}
#endif

	old_slash = strrchr(old_path, '/');
	new_slash = strrchr(new_path, '/');

	/* Confine each side independently.  A *relative* side is a transfer path,
	 * confined beneath the tree via secure_relative_open().  An *absolute* side is
	 * an operator path (an absolute --temp-dir/--partial-dir temp file): resolve
	 * its parent via the ownership walk so a flipped foreign-owned parent symlink
	 * can't redirect the rename out of tree, while still allowing the operator's
	 * own dirs/".."/uid0-or-euid symlinks.  (--insecure-links keeps the legacy
	 * unconfined AT_FDCWD path.)  Doing each side independently means an absolute
	 * source never disables confinement of a relative destination. */
	if (*old_path == '/') {
#if defined O_NOFOLLOW && defined O_DIRECTORY
		if (!symlink_optout_allowed()) {
			operator_path_resolve = 1;	/* operator side: enforce module-exclude */
			old_dfd = owner_walk_parent(old_path, &old_bname);
			operator_path_resolve = 0;
			if (old_dfd < 0)
				return -1;
			old_owns = True;
		} else
#endif
			old_bname = old_path;
	} else if (old_slash) {
		old_dlen = old_slash - old_path;
		if (old_dlen >= sizeof old_dirpath) {
			errno = ENAMETOOLONG;
			return -1;
		}
		memcpy(old_dirpath, old_path, old_dlen);
		old_dirpath[old_dlen] = '\0';
		old_bname = old_slash + 1;
		old_dfd = secure_relative_open(NULL, old_dirpath, O_RDONLY | O_DIRECTORY, 0);
		if (old_dfd < 0)
			return -1;
		old_owns = True;
	} else {
		old_bname = old_path;
	}

	if (*new_path == '/') {
#if defined O_NOFOLLOW && defined O_DIRECTORY
		if (!symlink_optout_allowed()) {
			operator_path_resolve = 1;	/* operator side: enforce module-exclude */
			new_dfd = owner_walk_parent(new_path, &new_bname);
			operator_path_resolve = 0;
			if (new_dfd < 0) {
				e = errno;
				if (old_owns) close(old_dfd);
				errno = e;
				return -1;
			}
			new_owns = True;
		} else
#endif
			new_bname = new_path;
	} else if (new_slash) {
		new_dlen = new_slash - new_path;
		if (new_dlen >= sizeof new_dirpath) {
			e = ENAMETOOLONG;
			if (old_owns) close(old_dfd);
			errno = e;
			return -1;
		}
		memcpy(new_dirpath, new_path, new_dlen);
		new_dirpath[new_dlen] = '\0';
		new_bname = new_slash + 1;
		if (old_owns && old_dlen == new_dlen
		 && memcmp(old_dirpath, new_dirpath, old_dlen) == 0) {
			new_dfd = old_dfd;
		} else {
			new_dfd = secure_relative_open(NULL, new_dirpath, O_RDONLY | O_DIRECTORY, 0);
			if (new_dfd < 0) {
				e = errno;
				if (old_owns) close(old_dfd);
				errno = e;
				return -1;
			}
			new_owns = True;
		}
	} else {
		new_bname = new_path;
	}

	ret = renameat(old_dfd, old_bname, new_dfd, new_bname);
	e = errno;
	if (new_owns)
		close(new_dfd);
	if (old_owns)
		close(old_dfd);
	errno = e;
	return ret;
#else
	return do_rename(old_path, new_path);
#endif
}

#ifdef HAVE_FTRUNCATE
int do_ftruncate(int fd, OFF_T size)
{
	int ret;

	if (dry_run) return 0;
	RETURN_ERROR_IF_RO_OR_LO;

	do {
		ret = ftruncate(fd, size);
	} while (ret < 0 && errno == EINTR);

	return ret;
}
#endif

void trim_trailing_slashes(char *name)
{
	int l;
	/* Some BSD systems cannot make a directory if the name
	 * contains a trailing slash.
	 * <http://www.opensource.apple.com/bugs/X/BSD%20Kernel/2734739.html> */

	/* Don't change empty string; and also we can't improve on
	 * "/" */

	l = strlen(name);
	while (l > 1) {
		if (name[--l] != '/')
			break;
		name[l] = '\0';
	}
}

int do_mkdir(char *path, mode_t mode)
{
	if (dry_run) return 0;
	RETURN_ERROR_IF_RO_OR_LO;
	RETURN_ERROR_IF_NULL(path);
	trim_trailing_slashes(path);
	return mkdir(path, mode);
}

/*
  Symlink-race-safe variant of do_mkdir() for receiver-side use. See
  the comment on do_chmod_at() for the threat model and design rationale.

  mkdir() resolves parent symlinks at every component, so a parent-
  component swap can place an attacker-named directory outside the
  module. Defence: open the parent of fname under secure_relative_open()
  and call mkdirat() against that dirfd.

  Mutates path in place to trim trailing slashes (matches do_mkdir()).
  Falls through to do_mkdir() in dry-run, non-daemon, chrooted, no-
  parent and absolute-path cases.
*/
int do_mkdir_at(char *path, mode_t mode)
{
#ifdef AT_FDCWD
	extern int am_daemon, am_chrooted;
	char dirpath[MAXPATHLEN];
	const char *bname;
	const char *slash;
	int dfd, ret, e;
	size_t dlen;

	if (dry_run) return 0;
	RETURN_ERROR_IF_RO_OR_LO;
	RETURN_ERROR_IF_NULL(path);
	trim_trailing_slashes(path);

#if defined O_NOFOLLOW && defined O_DIRECTORY
	if (operator_path_resolve) {
		if (symlink_optout_allowed())
			return mkdir(path, mode);
		dfd = owner_walk_parent(path, &bname);
		if (dfd < 0)
			return -1;
		ret = mkdirat(dfd, bname, mode);
		e = errno;
		close(dfd);
		errno = e;
		return ret;
	}
#endif

	if (!secure_relpath_active())
		return mkdir(path, mode);

	if (!path || !*path || *path == '/')
		return mkdir(path, mode);

	slash = strrchr(path, '/');
	if (!slash)
		return mkdir(path, mode);

	dlen = slash - path;
	if (dlen >= sizeof dirpath) {
		errno = ENAMETOOLONG;
		return -1;
	}
	memcpy(dirpath, path, dlen);
	dirpath[dlen] = '\0';
	bname = slash + 1;

	dfd = secure_relative_open(NULL, dirpath, O_RDONLY | O_DIRECTORY, 0);
	if (dfd < 0)
		return -1;

	ret = mkdirat(dfd, bname, mode);
	e = errno;
	close(dfd);
	errno = e;
	return ret;
#else
	return do_mkdir(path, mode);
#endif
}

/* like mkstemp but forces permissions */
int do_mkstemp(char *template, mode_t perms)
{
	RETURN_ERROR_IF(dry_run, 0);
	RETURN_ERROR_IF(read_only, EROFS);
	perms |= S_IWUSR;

#if defined HAVE_SECURE_MKSTEMP && defined HAVE_FCHMOD && (!defined HAVE_OPEN64 || defined HAVE_MKSTEMP64)
	{
		int fd = mkstemp(template);
		if (fd == -1)
			return -1;
		if (fchmod(fd, perms) != 0 && preserve_perms) {
			int errno_save = errno;
			close(fd);
			unlink(template);
			errno = errno_save;
			return -1;
		}
#if defined HAVE_SETMODE && O_BINARY
		setmode(fd, O_BINARY);
#endif
		return fd;
	}
#else
	if (!mktemp(template))
		return -1;
	return do_open(template, O_RDWR|O_EXCL|O_CREAT, perms);
#endif
}

int do_stat(const char *path, STRUCT_STAT *st)
{
	RETURN_ERROR_IF_NULL(path);
#ifdef USE_STAT64_FUNCS
	return stat64(path, st);
#else
	return stat(path, st);
#endif
}

int do_lstat(const char *path, STRUCT_STAT *st)
{
	RETURN_ERROR_IF_NULL(path);
#ifdef SUPPORT_LINKS
# ifdef USE_STAT64_FUNCS
	return lstat64(path, st);
# else
	return lstat(path, st);
# endif
#else
	return do_stat(path, st);
#endif
}

/*
  Symlink-race-safe variants of do_stat() / do_lstat() for receiver-
  side use. See the comment on do_chmod_at() for the threat model.
  stat() and lstat() resolve parent components, so a parent-symlink
  swap can make the receiver's stat see attributes of a victim file
  outside the module -- which then drives later behaviour (e.g.
  "this isn't a directory, delete it" -> attacker-controlled unlink
  on something outside the module).

  Defence: open the parent under secure_relative_open() and use
  fstatat() with AT_SYMLINK_NOFOLLOW (lstat) or 0 (stat) against
  that dirfd. Same fall-through gating as the other wrappers.
*/
static int do_xstat_at(const char *path, STRUCT_STAT *st, int at_flags, int (*fallback)(const char *, STRUCT_STAT *))
{
#ifdef AT_FDCWD
	extern int am_daemon, am_chrooted;
	char dirpath[MAXPATHLEN];
	const char *bname;
	const char *slash;
	int dfd, ret, e;
	size_t dlen;

#if defined O_NOFOLLOW && defined O_DIRECTORY
	if (operator_path_resolve) {
		if (symlink_optout_allowed())
			return fallback(path, st);
		dfd = owner_walk_parent(path, &bname);
		if (dfd < 0)
			return -1;
		ret = fstatat(dfd, bname, st, at_flags);
		e = errno;
		close(dfd);
		errno = e;
		return ret;
	}
#endif

	if (!secure_relpath_active())
		return fallback(path, st);

	if (!path || !*path || *path == '/')
		return fallback(path, st);

	slash = strrchr(path, '/');
	if (!slash)
		return fallback(path, st);

	dlen = slash - path;
	if (dlen >= sizeof dirpath) {
		errno = ENAMETOOLONG;
		return -1;
	}
	memcpy(dirpath, path, dlen);
	dirpath[dlen] = '\0';
	bname = slash + 1;

	dfd = secure_relative_open(NULL, dirpath, O_RDONLY | O_DIRECTORY, 0);
	if (dfd < 0)
		return -1;

	ret = fstatat(dfd, bname, st, at_flags);
	e = errno;
	close(dfd);
	errno = e;
	return ret;
#else
	(void)at_flags;
	return fallback(path, st);
#endif
}

int do_stat_at(const char *path, STRUCT_STAT *st)
{
	return do_xstat_at(path, st, 0, do_stat);
}

int do_lstat_at(const char *path, STRUCT_STAT *st)
{
#if defined SUPPORT_LINKS && defined AT_FDCWD && defined AT_SYMLINK_NOFOLLOW
	return do_xstat_at(path, st, AT_SYMLINK_NOFOLLOW, do_lstat);
#elif defined SUPPORT_LINKS
	return do_lstat(path, st);
#else
	return do_xstat_at(path, st, 0, do_stat);
#endif
}

int do_fstat(int fd, STRUCT_STAT *st)
{
#ifdef USE_STAT64_FUNCS
	return fstat64(fd, st);
#else
	return fstat(fd, st);
#endif
}

OFF_T do_lseek(int fd, OFF_T offset, int whence)
{
#ifdef HAVE_LSEEK64
	return lseek64(fd, offset, whence);
#else
	return lseek(fd, offset, whence);
#endif
}

#ifdef HAVE_SETATTRLIST
int do_setattrlist_times(const char *path, STRUCT_STAT *stp)
{
	struct attrlist attrList;
	struct timespec ts[2];

	if (dry_run) return 0;
	RETURN_ERROR_IF_RO_OR_LO;

	/* setattrlist() takes a raw path and follows parent symlinks
	 * (FSOPT_NOFOLLOW only blocks the final component). When hardened
	 * resolution is active -- secure_relpath_active(): any non-chroot
	 * daemon/receiver module, plus a /./ inner-module chroot -- return
	 * ENOSYS so set_times()' tier walk falls through to do_utimensat_at(),
	 * which routes the update through a secure parent dirfd. The attribute
	 * set this would have used (ATTR_CMN_MODTIME / ATTR_CMN_ACCTIME) is the
	 * same set utimensat() handles, so no functionality is lost. */
	if (secure_relpath_active()) {
		errno = ENOSYS;
		return -1;
	}

	/* Yes, this is in the opposite order of utime and similar. */
	ts[0].tv_sec = stp->st_mtime;
	ts[0].tv_nsec = stp->ST_MTIME_NSEC;

	ts[1].tv_sec = stp->st_atime;
	ts[1].tv_nsec = stp->ST_ATIME_NSEC;

	memset(&attrList, 0, sizeof attrList);
	attrList.bitmapcount = ATTR_BIT_MAP_COUNT;
	attrList.commonattr = ATTR_CMN_MODTIME | ATTR_CMN_ACCTIME;
	return setattrlist(path, &attrList, ts, sizeof ts, FSOPT_NOFOLLOW);
}

#ifdef SUPPORT_CRTIMES
int do_setattrlist_crtime(const char *path, time_t crtime)
{
	struct attrlist attrList;
	struct timespec ts;

	if (dry_run) return 0;
	RETURN_ERROR_IF_RO_OR_LO;

	/* setattrlist() is path-based and follows parent symlinks
	 * (FSOPT_NOFOLLOW only blocks the final component), and macOS has no
	 * at-aware variant targeting ATTR_CMN_CRTIME.  As with POSIX ACLs where
	 * the OS offers no race-safe primitive, we keep --crtimes functional
	 * (daemon and non-daemon) and accept the parent-component symlink race
	 * as a documented residual rather than dropping crtime.  A daemon
	 * operator who does not want that residual can disable the feature with
	 * "refuse options = crtimes" in rsyncd.conf. */
	ts.tv_sec = crtime;
	ts.tv_nsec = 0;

	memset(&attrList, 0, sizeof attrList);
	attrList.bitmapcount = ATTR_BIT_MAP_COUNT;
	attrList.commonattr = ATTR_CMN_CRTIME;
	return setattrlist(path, &attrList, &ts, sizeof ts, FSOPT_NOFOLLOW);
}
#endif
#endif /* HAVE_SETATTRLIST */

#ifdef SUPPORT_CRTIMES
time_t get_create_time(const char *path, STRUCT_STAT *stp)
{
#ifdef HAVE_GETATTRLIST
	static struct create_time attrBuf;
	struct attrlist attrList;

	(void)stp;
	/* getattrlist() is path-based and follows parent symlinks; like
	 * do_setattrlist_crtime() there is no race-safe variant, so reading the
	 * source crtime stays functional and the parent-component symlink race
	 * is an accepted residual (refusable via "refuse options = crtimes"). */
	memset(&attrList, 0, sizeof attrList);
	attrList.bitmapcount = ATTR_BIT_MAP_COUNT;
	attrList.commonattr = ATTR_CMN_CRTIME;
	if (getattrlist(path, &attrList, &attrBuf, sizeof attrBuf, FSOPT_NOFOLLOW) < 0)
		return 0;
	return attrBuf.crtime.tv_sec;
#elif defined __CYGWIN__
	(void)path;
	return stp->st_birthtime;
#else
#error Unknown crtimes implementation
#endif
}

#if defined __CYGWIN__
int do_SetFileTime(const char *path, time_t crtime)
{
	if (dry_run) return 0;
	RETURN_ERROR_IF_RO_OR_LO;

	int cnt = MultiByteToWideChar(CP_UTF8, 0, path, -1, NULL, 0);
	if (cnt == 0)
	    return -1;
	WCHAR *pathw = new_array(WCHAR, cnt);
	if (!pathw)
	    return -1;
	MultiByteToWideChar(CP_UTF8, 0, path, -1, pathw, cnt);
	HANDLE handle = CreateFileW(pathw, FILE_WRITE_ATTRIBUTES, FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
				    NULL, OPEN_EXISTING, FILE_FLAG_BACKUP_SEMANTICS, NULL);
	free(pathw);
	if (handle == INVALID_HANDLE_VALUE)
	    return -1;
	int64 temp_time = (crtime * 10000000LL) + 116444736000000000LL;
	FILETIME birth_time;
	birth_time.dwLowDateTime = (DWORD)temp_time;
	birth_time.dwHighDateTime = (DWORD)(temp_time >> 32);
	int ok = SetFileTime(handle, &birth_time, NULL, NULL);
	CloseHandle(handle);
	return ok ? 0 : -1;
}
#endif
#endif /* SUPPORT_CRTIMES */

#ifdef HAVE_UTIMENSAT
int do_utimensat(const char *path, STRUCT_STAT *stp)
{
	struct timespec t[2];

	if (dry_run) return 0;
	RETURN_ERROR_IF_RO_OR_LO;
	RETURN_ERROR_IF_NULL(path);

	t[0].tv_sec = stp->st_atime;
#ifdef ST_ATIME_NSEC
	t[0].tv_nsec = stp->ST_ATIME_NSEC;
#else
	t[0].tv_nsec = 0;
#endif
	t[1].tv_sec = stp->st_mtime;
#ifdef ST_MTIME_NSEC
	t[1].tv_nsec = stp->ST_MTIME_NSEC;
#else
	t[1].tv_nsec = 0;
#endif
	return utimensat(AT_FDCWD, path, t, AT_SYMLINK_NOFOLLOW);
}

/*
  Symlink-race-safe variant of do_utimensat() for receiver-side use.
  See the comment on do_chmod_at() for the threat model. utimes()
  resolves parent components and follows a final-component symlink;
  lutimes() doesn't follow the final component but still resolves
  parents. Either way, a parent-symlink swap can redirect the
  timestamp update outside the module. Defence: open the parent of
  path under secure_relative_open() and call utimensat() with
  AT_SYMLINK_NOFOLLOW against that dirfd.

  Falls through to do_utimensat() in the same dry-run / non-daemon /
  chrooted / no-parent / absolute-path cases as the other wrappers.
  Returns -1 with errno=ENOSYS on systems without utimensat()
  (caller is expected to fall back to the legacy tier walk).
*/
int do_utimensat_at(const char *path, STRUCT_STAT *stp)
{
#ifdef AT_FDCWD
	extern int am_daemon, am_chrooted;
	struct timespec t[2];
	char dirpath[MAXPATHLEN];
	const char *bname;
	const char *slash;
	int dfd, ret, e;
	size_t dlen;

	if (dry_run) return 0;
	RETURN_ERROR_IF_RO_OR_LO;

	if (!secure_relpath_active())
		return do_utimensat(path, stp);

	if (!path || !*path || *path == '/')
		return do_utimensat(path, stp);

	slash = strrchr(path, '/');
	if (!slash)
		return do_utimensat(path, stp);

	dlen = slash - path;
	if (dlen >= sizeof dirpath) {
		errno = ENAMETOOLONG;
		return -1;
	}
	memcpy(dirpath, path, dlen);
	dirpath[dlen] = '\0';
	bname = slash + 1;

	t[0].tv_sec = stp->st_atime;
#ifdef ST_ATIME_NSEC
	t[0].tv_nsec = stp->ST_ATIME_NSEC;
#else
	t[0].tv_nsec = 0;
#endif
	t[1].tv_sec = stp->st_mtime;
#ifdef ST_MTIME_NSEC
	t[1].tv_nsec = stp->ST_MTIME_NSEC;
#else
	t[1].tv_nsec = 0;
#endif

	dfd = secure_relative_open(NULL, dirpath, O_RDONLY | O_DIRECTORY, 0);
	if (dfd < 0)
		return -1;

	ret = utimensat(dfd, bname, t, AT_SYMLINK_NOFOLLOW);
	e = errno;
	close(dfd);
	errno = e;
	return ret;
#else
	return do_utimensat(path, stp);
#endif
}
#endif

#ifdef HAVE_LUTIMES
int do_lutimes(const char *path, STRUCT_STAT *stp)
{
	struct timeval t[2];

	if (dry_run) return 0;
	RETURN_ERROR_IF_RO_OR_LO;

	t[0].tv_sec = stp->st_atime;
#ifdef ST_ATIME_NSEC
	t[0].tv_usec = stp->ST_ATIME_NSEC / 1000;
#else
	t[0].tv_usec = 0;
#endif
	t[1].tv_sec = stp->st_mtime;
#ifdef ST_MTIME_NSEC
	t[1].tv_usec = stp->ST_MTIME_NSEC / 1000;
#else
	t[1].tv_usec = 0;
#endif
	return lutimes(path, t);
}
#endif

#ifdef HAVE_UTIMES
int do_utimes(const char *path, STRUCT_STAT *stp)
{
	struct timeval t[2];

	if (dry_run) return 0;
	RETURN_ERROR_IF_RO_OR_LO;

	t[0].tv_sec = stp->st_atime;
#ifdef ST_ATIME_NSEC
	t[0].tv_usec = stp->ST_ATIME_NSEC / 1000;
#else
	t[0].tv_usec = 0;
#endif
	t[1].tv_sec = stp->st_mtime;
#ifdef ST_MTIME_NSEC
	t[1].tv_usec = stp->ST_MTIME_NSEC / 1000;
#else
	t[1].tv_usec = 0;
#endif
	return utimes(path, t);
}

#elif defined HAVE_UTIME
int do_utime(const char *path, STRUCT_STAT *stp)
{
#ifdef HAVE_STRUCT_UTIMBUF
	struct utimbuf tbuf;
#else
	time_t t[2];
#endif

	if (dry_run) return 0;
	RETURN_ERROR_IF_RO_OR_LO;

# ifdef HAVE_STRUCT_UTIMBUF
	tbuf.actime = stp->st_atime;
	tbuf.modtime = stp->st_mtime;
	return utime(path, &tbuf);
# else
	t[0] = stp->st_atime;
	t[1] = stp->st_mtime;
	return utime(path, t);
# endif
}

#else
#error Need utimes or utime function.
#endif

#ifdef SUPPORT_PREALLOCATION
#ifdef FALLOC_FL_KEEP_SIZE
#define DO_FALLOC_OPTIONS FALLOC_FL_KEEP_SIZE
#else
#define DO_FALLOC_OPTIONS 0
#endif

OFF_T do_fallocate(int fd, OFF_T offset, OFF_T length)
{
	/* FALLOC_FL_KEEP_SIZE lets --preallocate/--inplace keep the file size at 0
	 * until data is written, but a later hole-punch (for --sparse) can only
	 * deallocate blocks that lie within the file's size -- with KEEP_SIZE the
	 * reserved blocks sit beyond EOF and the punch silently does nothing,
	 * leaving the file fully allocated.  So when holes will also be punched,
	 * preallocate at full size instead (write_sparse then punches the nulls). */
	int opts = (inplace || preallocate_files) && sparse_files <= 0 ? DO_FALLOC_OPTIONS : 0;
	int ret;
	RETURN_ERROR_IF(dry_run, 0);
	RETURN_ERROR_IF_RO_OR_LO;
	if (length & 1) /* make the length not match the desired length */
		length++;
	else
		length--;
#if defined HAVE_FALLOCATE
	ret = fallocate(fd, opts, offset, length);
#elif defined HAVE_SYS_FALLOCATE
	ret = syscall(SYS_fallocate, fd, opts, (loff_t)offset, (loff_t)length);
#elif defined HAVE_EFFICIENT_POSIX_FALLOCATE
	ret = posix_fallocate(fd, offset, length);
#else
#error Coding error in SUPPORT_PREALLOCATION logic.
#endif
	if (ret < 0)
		return ret;
	if (opts == 0) {
		STRUCT_STAT st;
		if (do_fstat(fd, &st) < 0)
			return length;
		return st.st_blocks * S_BLKSIZE;
	}
	/* With FALLOC_FL_KEEP_SIZE the blocks for [0, length) are reserved even
	 * though the file size stays put.  Return that reserved length (not 0) so
	 * the caller's preallocated_len is meaningful: write_sparse() needs it to
	 * choose do_punch_hole() over a plain lseek() when turning a null run into
	 * a hole, and the receiver uses it to trim any over-preallocation.  (A
	 * stray 0 here, from 2019's switch to KEEP_SIZE, is why --preallocate
	 * --sparse stopped producing sparse files.) */
	return length;
}
#endif

/* Punch a hole at pos for len bytes. The current file position must be at pos and will be
 * changed to be at pos + len. */
int do_punch_hole(int fd, OFF_T pos, OFF_T len)
{
#ifdef HAVE_FALLOCATE
# ifdef HAVE_FALLOC_FL_PUNCH_HOLE
	if (fallocate(fd, FALLOC_FL_PUNCH_HOLE | FALLOC_FL_KEEP_SIZE, pos, len) == 0) {
		if (do_lseek(fd, len, SEEK_CUR) != pos + len)
			return -1;
		return 0;
	}
# endif
# ifdef HAVE_FALLOC_FL_ZERO_RANGE
	if (fallocate(fd, FALLOC_FL_ZERO_RANGE, pos, len) == 0) {
		if (do_lseek(fd, len, SEEK_CUR) != pos + len)
			return -1;
		return 0;
	}
# endif
#else
	(void)pos;
#endif
	{
		char zeros[4096];
		memset(zeros, 0, sizeof zeros);
		while (len > 0) {
			int chunk = len > (int)sizeof zeros ? (int)sizeof zeros : len;
			int wrote = write(fd, zeros, chunk);
			if (wrote <= 0) {
				if (wrote < 0 && errno == EINTR)
					continue;
				return -1;
			}
			len -= wrote;
		}
	}
	return 0;
}

int do_open_nofollow(const char *pathname, int flags)
{
#ifndef O_NOFOLLOW
	STRUCT_STAT f_st, l_st;
#endif
	int fd;

	if (flags != O_RDONLY) {
		RETURN_ERROR_IF(dry_run, 0);
		RETURN_ERROR_IF_RO_OR_LO;
#ifndef O_NOFOLLOW
		/* This function doesn't support write attempts w/o O_NOFOLLOW. */
		errno = EINVAL;
		return -1;
#endif
	}

#ifdef O_NOATIME
	if (open_noatime)
		flags |= O_NOATIME;
#endif

#ifdef O_NOFOLLOW
	fd = open(pathname, flags|O_NOFOLLOW);
#else
	if (do_lstat(pathname, &l_st) < 0)
		return -1;
	if (S_ISLNK(l_st.st_mode)) {
		errno = ELOOP;
		return -1;
	}
	if ((fd = open(pathname, flags)) < 0)
		return fd;
	if (do_fstat(fd, &f_st) < 0) {
	  close_and_return_error:
		{
			int save_errno = errno;
			close(fd);
			errno = save_errno;
		}
		return -1;
	}
	if (l_st.st_dev != f_st.st_dev || l_st.st_ino != f_st.st_ino) {
		errno = EINVAL;
		goto close_and_return_error;
	}
#endif

	return fd;
}

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

/* Returns 1 if path has any "/"-separated component that is exactly
 * "..", 0 otherwise. Used by secure_relative_open's front-door
 * validation to reject ".." inputs (bare "..", "foo/..", "subdir/..")
 * for non-re-anchored paths; the walk itself resolves an in-tree ".."
 * safely (ds_descend pops to the held parent) for a re-anchored path. */
static int path_has_dotdot_component(const char *path)
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

/* The logical current directory (maintained by change_dir() in util1.c).
 * Defined here -- rather than in util1.c -- so the test helpers that link
 * syscall.o but not util1.o (tls, trimslash) get the definition without a
 * weak-symbol fallback, which is not portable to PE/COFF targets (Cygwin). */
char curr_dir[MAXPATHLEN];
unsigned int curr_dir_len;

#if defined(O_NOFOLLOW) && defined(O_DIRECTORY) && defined(AT_FDCWD)
/* In-tree symlink following for secure_relative_open()'s directory walk (below).
 * An early version refused every symlink with O_NOFOLLOW on each component, which
 * broke legitimate within-tree directory symlinks (--keep-dirlinks #715, and -aR
 * through a symlinked parent); the walk now follows them safely.
 *
 * A directory walk keeps a stack of the open dirfds from the anchor (index 0,
 * borrowed -- not closed here) down to the current directory.  Descending into
 * a real subdirectory pushes its fd; a ".." in a followed symlink target pops
 * back to the already-pinned parent fd rather than re-resolving ".." with
 * openat(), so an ancestor renamed mid-walk cannot redirect the climb, and the
 * climb can never rise above the anchor (a pop at the anchor returns ELOOP).
 * This matches RESOLVE_BENEATH, which allows in-tree ".." that stays beneath the
 * root.  Absolute symlink targets are refused; symlink hops are bounded. */
#ifndef SECURE_OPEN_MAXSYMLINKS
#define SECURE_OPEN_MAXSYMLINKS 40
#endif

/* Max directory levels held open at once during a single resolve.  The walk
 * holds one fd per component, so depth is bounded by RLIMIT_NOFILE anyway; a
 * fixed array (no malloc/realloc) keeps the stack simple and the static
 * analyzer happy.  Mirrors DPC_MAXDEPTH's fixed-cap approach. */
#define DS_MAXDEPTH 1024

struct dirstack {
	int fds[DS_MAXDEPTH];	/* fds[0] = anchor (borrowed); fds[top] = current dir */
	int top;
	/* Absolute path of fds[top], maintained as we descend/pop, for the
	 * exclude-aware refusal (abspath_outside_confinement).  Empty unless the
	 * caller seeds it with the anchor's absolute path; then a followed symlink
	 * that redirects the walk into a module-excluded dir is refused. */
	char abspath[MAXPATHLEN];
};

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

/* Initialise with `anchor` (which may be AT_FDCWD) as the un-owned base.
 * Returns int for caller symmetry, but cannot fail (the fd array is inline). */
static int ds_init(struct dirstack *ds, int anchor)
{
	ds->abspath[0] = '\0';
	ds->fds[0] = anchor;
	ds->top = 0;
	return 0;
}

/* Close every pushed fd (but not the borrowed anchor at index 0). */
static void ds_free(struct dirstack *ds)
{
	while (ds->top > 0)
		close(ds->fds[ds->top--]);
}

static int ds_cur(struct dirstack *ds)
{
	return ds->fds[ds->top];
}

static int ds_push(struct dirstack *ds, int fd)
{
	if (ds->top + 1 >= DS_MAXDEPTH) {	/* deeper than we'll hold open */
		close(fd);
		errno = ENOMEM;
		return -1;
	}
	ds->fds[++ds->top] = fd;
	return 0;
}

/* Detach the current dir as an owned fd the caller must close.  At the anchor
 * (top 0) the anchor is borrowed, so return a fresh dup of it instead. */
static int ds_take(struct dirstack *ds)
{
	if (ds->top > 0)
		return ds->fds[ds->top--];
	return openat(ds->fds[0], ".", O_RDONLY | O_DIRECTORY);
}

static int ds_walk_path(struct dirstack *ds, char *path, int *hops);

/* Descend one path component on the stack: "." stays, ".." pops to the pinned
 * parent (ELOOP at the anchor), a real subdirectory is pushed, and an in-tree
 * directory symlink is followed by walking its (relative, possibly
 * ..-containing) target on the same stack.  Returns 0, or -1 with errno set:
 * ELOOP for a refused/escaping symlink or a hop overrun, otherwise the
 * underlying openat()/readlinkat() errno (ENOENT, a real ENOTDIR, EACCES). */
static int ds_descend(struct dirstack *ds, const char *part, int *hops)
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
		if (abspath_outside_confinement(ds->abspath)) {
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
static int ds_walk_path(struct dirstack *ds, char *path, int *hops)
{
	char *save = NULL;
	for (char *c = strtok_r(path, "/", &save); c; c = strtok_r(NULL, "/", &save)) {
		if (ds_descend(ds, c, hops) < 0)
			return -1;
	}
	return 0;
}

/* Walk `relpath` confined beneath the borrowed anchor dirfd (which may be
 * AT_FDCWD) and return the opened leaf fd, or -1.  Does NOT close `anchor_fd` --
 * the caller owns it.  Shared by secure_relative_open() (which first resolves a
 * basedir to the anchor) and secure_relative_open_at() (handed an already-open
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

		/* A literal "." or ".." is a movement, not a name to open.  It must go
		 * through ds_descend(), which refuses to pop above the anchor, BEFORE
		 * the leaf fast paths below -- those openat() the component directly,
		 * so a final ".." would otherwise hand back the anchor's own parent
		 * (with O_NOFOLLOW) or open it transiently (without O_DIRECTORY). */
		if (part[0] == '.'
		 && (part[1] == '\0' || (part[1] == '.' && part[2] == '\0'))) {
			if (ds_descend(&ds, part, hops) < 0)
				goto cleanup;
			if (is_last) {
				if (flags & O_DIRECTORY)
					retfd = ds_take(&ds);
				else
					errno = EISDIR;
				goto cleanup;
			}
			continue;
		}

		/* File leaf (final component, caller did not ask for O_DIRECTORY):
		 * never follow a symlink leaf. */
		if (is_last && !(flags & O_DIRECTORY)) {
			if (ds.abspath[0]) {
				char leafabs[MAXPATHLEN];
				if (snprintf(leafabs, sizeof leafabs, "%s/%s", ds.abspath, part)
				      < (int)sizeof leafabs
				 && abspath_outside_confinement(leafabs)) {
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

int secure_relative_open(const char *basedir, const char *relpath, int flags, mode_t mode)
{
	extern int am_daemon, am_chrooted;
	extern char *module_dir;
	extern unsigned int module_dirlen;
	char modrel_buf[MAXPATHLEN];
	int reanchored = 0;

	if (!relpath || relpath[0] == '/') {
		// must be a relative path
		errno = EINVAL;
		return -1;
	}

	/* Sanitizing daemon (am_daemon && !am_chrooted) and the /./ inner-module
	 * chroot (am_daemon && am_chrooted && module_dirlen) -- both keep the module
	 * root, not the cwd, as the trust boundary.  Here we have chdir'd into a
	 * sub-dir of the module (the transfer destination), so a relative alt-dest
	 * like "../01" may legitimately climb to a sibling that is still inside the
	 * module (#915).  Confining beneath the cwd would reject that climb.
	 * Re-anchor at the module root by prefixing the cwd's module-relative path
	 * (from rsync's logical curr_dir[], a guaranteed lexical prefix of
	 * module_dir, unlike getcwd()) and resolving beneath module_dir; RESOLVE_
	 * BENEATH then allows in-module climbs and still rejects escapes.  Only for
	 * paths that contain "..".  module_dirlen is 0 for a `path = /` module
	 * (clientserver.c), so the non-chroot arm gates on module_dir, not its
	 * length, to cover that case too -- the prefix check below treats
	 * module_dirlen 0 as "module root is /". */
	if (am_daemon && (!am_chrooted || module_dirlen)
	 && module_dir && module_dir[0] == '/'
	 && (basedir == NULL || basedir[0] != '/')
	 && (path_has_dotdot_component(relpath)
	  || (basedir && path_has_dotdot_component(basedir)))) {
		const char *p;
		int n;
		if (curr_dir_len >= module_dirlen
		 && strncmp(curr_dir, module_dir, module_dirlen) == 0
		 && (curr_dir[module_dirlen] == '\0' || curr_dir[module_dirlen] == '/')) {
			for (p = curr_dir + module_dirlen; *p == '/'; p++) {}
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
			basedir = module_dir;	/* absolute, operator-trusted anchor */
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
	const char *anchor_abspath = !basedir ? curr_dir
				   : (basedir[0] == '/' ? basedir : NULL);
	int retfd = secure_walk_at(dirfd, anchor_abspath, relpath, flags, mode, &hops);
	if (dirfd != AT_FDCWD)
		close(dirfd);
	return retfd;
#endif // O_NOFOLLOW, O_DIRECTORY
}

/* Common fd-anchored resolver.  A caller may explicitly allow literal ".."
 * components when the fd itself is the confinement boundary: secure_walk_at()
 * resolves each one by popping its held-dirfd stack and refuses a pop above the
 * anchor.  Other callers retain the front-door validation used by
 * secure_relative_open(). */
static int secure_relative_open_at_internal(int anchor_fd, const char *relpath,
					    int flags, mode_t mode, int allow_dotdot)
{
#if !defined(O_NOFOLLOW) || !defined(O_DIRECTORY) || !defined(AT_FDCWD)
	(void)anchor_fd; (void)relpath; (void)flags; (void)mode; (void)allow_dotdot;
	errno = ENOSYS;
	return -1;
#else
	int hops = SECURE_OPEN_MAXSYMLINKS;
	if (!relpath || relpath[0] == '/') {
		errno = EINVAL;
		return -1;
	}
	if (!allow_dotdot && path_has_dotdot_component(relpath)) {
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

/* Like secure_relative_open() but anchored at an already-open directory fd
 * (borrowed -- the caller keeps ownership) rather than a basedir path.  Lets a
 * caller pin the trust root once -- e.g. a daemon's module root opened while
 * still privileged -- and resolve a relative path beneath it without re-walking
 * the absolute path as a dropped-privilege uid.  The ordinary entry point keeps
 * rejecting literal ".." components as suspicious caller input. */
int secure_relative_open_at(int anchor_fd, const char *relpath, int flags, mode_t mode)
{
	return secure_relative_open_at_internal(anchor_fd, relpath, flags, mode, 0);
}

/* Resolve a path that may contain literal ".." beneath a trusted anchor fd.
 * Used for a followed symlink target, where parent-relative components are
 * normal pathname semantics.  The held-fd stack still refuses every escape
 * above anchor_fd. */
int secure_relative_open_at_beneath(int anchor_fd, const char *relpath,
				    int flags, mode_t mode)
{
	return secure_relative_open_at_internal(anchor_fd, relpath, flags, mode, 1);
}

#if defined O_NOFOLLOW && defined O_DIRECTORY && defined AT_FDCWD
/* Fill buf with len random bytes.  Prefers /dev/urandom for cryptographic
 * quality; falls back to rand() if /dev/urandom cannot be opened or read
 * (e.g. inside a chroot or container without /dev populated). */
static void rand_bytes(unsigned char *buf, size_t len)
{
#ifndef O_CLOEXEC
#define O_CLOEXEC 0
#endif
	int fd = open("/dev/urandom", O_RDONLY | O_CLOEXEC);
	if (fd >= 0) {
		ssize_t n = read(fd, buf, len);
		close(fd);
		if (n == (ssize_t)len) {
			return;
		}
	}
	for (size_t i = 0; i < len; i++) {
		buf[i] = (unsigned char)rand();
	}
}
#endif

/* Create a unique temp file directly in directory `dfd` for the held-dirfd
 * traversal: `filename` is the basename ending in "XXXXXX", rewritten in place
 * to the chosen name.  O_EXCL|O_NOFOLLOW so a planted name can't be followed or
 * clobbered.  Does NOT close dfd (the caller owns it).  Returns the fd, or -1.
 * This is the create loop shared with secure_mkstemp(). */
int do_mkstemp_atfd(int dfd, char *filename, mode_t perms)
{
#ifdef AT_FDCWD
	static const char letters[] = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789";
	size_t filename_len = strlen(filename);
	char *suffix;
	int fd = -1;

	if (filename_len < 6) {
		errno = EINVAL;
		return -1;
	}
	suffix = filename + filename_len - 6; /* Points to XXXXXX */
	if (strcmp(suffix, "XXXXXX") != 0) {
		errno = EINVAL;
		return -1;
	}

	perms |= S_IWUSR;
	for (int tries = 0; tries < 100; tries++) {
		unsigned char rbytes[6];
		rand_bytes(rbytes, sizeof(rbytes));
		for (int i = 0; i < 6; i++)
			suffix[i] = letters[rbytes[i] % (sizeof(letters) - 1)];

		fd = openat(dfd, filename, O_RDWR | O_CREAT | O_EXCL | O_NOFOLLOW, perms);
		if (fd >= 0)
			break;
		if (errno != EEXIST)
			return -1;
	}

	if (fd >= 0) {
		if (fchmod(fd, perms) != 0 && preserve_perms) {
			int errno_save = errno;
			close(fd);
			unlinkat(dfd, filename, 0);
			errno = errno_save;
			return -1;
		}
#if defined HAVE_SETMODE && O_BINARY
		setmode(fd, O_BINARY);
#endif
	}
	return fd;
#else
	(void)dfd; (void)filename; (void)perms;
	errno = ENOSYS;
	return -1;
#endif
}

/*
  Secure version of mkstemp that prevents symlink attacks on parent directories.
  Like secure_relative_open(), this walks the path checking each component
  with O_NOFOLLOW to prevent TOCTOU race conditions.

  The template may be relative or absolute, but must not contain ../ components.
  Returns fd on success, -1 on error.
*/
int secure_mkstemp(char *template, mode_t perms, int operator_path)
{
#if !defined(O_NOFOLLOW) || !defined(O_DIRECTORY) || !defined(AT_FDCWD)
	/* Fall back to regular mkstemp on old systems */
	return do_mkstemp(template, perms);
#else
	char *lastslash;
	int dirfd = AT_FDCWD;
	int fd = -1;

	if (!template) {
		errno = EINVAL;
		return -1;
	}
	if (strncmp(template, "../", 3) == 0 || strstr(template, "/../")) {
		errno = EINVAL;
		return -1;
	}

	/* An operator-supplied --temp-dir may point outside the tree; --insecure-links
	 * (or a daemon module's "insecure links =") restores legacy following. */
	if (operator_path && symlink_optout_allowed())
		return do_mkstemp(template, perms);

	/* Open the temp file's directory.  For an operator --temp-dir use the
	 * ownership walk (follow a uid0/euid-owned symlink, refuse a foreign one,
	 * absolute and relative alike); otherwise -- the deep-entry-dir fallback when
	 * the held-dirfd cache declines -- use the strict transfer-path resolver
	 * (refuse all symlinks, confine beneath the transfer root).  The temp file
	 * itself is created below with O_EXCL|O_NOFOLLOW, so a planted name can't be
	 * followed either way. */
	lastslash = strrchr(template, '/');
	if (lastslash) {
		char dirbuf[MAXPATHLEN];
		size_t dlen = lastslash - template;
		const char *dir;
		if (dlen == 0)
			dir = "/";
		else {
			if (dlen >= sizeof dirbuf) {
				errno = ENAMETOOLONG;
				return -1;
			}
			memcpy(dirbuf, template, dlen);
			dirbuf[dlen] = '\0';
			dir = dirbuf;
		}
		dirfd = operator_path
		      ? open_no_attacker_symlinks(dir, O_RDONLY | O_DIRECTORY, 0)
		      : secure_relative_open(dir, ".", O_RDONLY | O_DIRECTORY, 0);
		if (dirfd < 0)
			return -1;
	}

	/* Create the temp file in the securely-opened directory. */
	{
		char *filename = lastslash ? lastslash + 1 : template;
		int e;
		fd = do_mkstemp_atfd(dirfd, filename, perms);
		e = errno;
		if (dirfd != AT_FDCWD) close(dirfd);
		errno = e;
	}
	return fd;
#endif
}

/*
  varient of do_open/do_open_nofollow which does do_open() if the
  copy_links or copy_unsafe_links options are set and does
  do_open_nofollow() otherwise

  This is used to prevent a race condition where an attacker could be
  switching a file between being a symlink and being a normal file

  The open is always done with O_RDONLY flags
 */
int do_open_checklinks(const char *pathname)
{
	if (copy_links || copy_unsafe_links) {
		return do_open(pathname, O_RDONLY, 0);
	}
	return do_open_nofollow(pathname, O_RDONLY);
}

/* Held-directory-fd traversal.
 *
 * Rather than re-resolve a full path on every syscall (do_*_at() re-opens the
 * parent via secure_relative_open() each call), the generator and receiver
 * open each directory ONCE via open_dir_secure() and issue single-component
 * *at() ops against that held dirfd with the do_*_atfd() wrappers below.  The
 * parent is a pinned fd, not re-resolved, so the per-entry symlink-race window
 * is closed and the re-resolution overhead is gone.
 *
 * open_dir_secure() owns both the authority gate and the resolver choice: it
 * returns a held dirfd only when hardened resolution is in effect, else -1
 * with errno==0 so the caller falls back to the do_*_at() wrappers
 * (behaviour-neutral).  The do_*_atfd() wrappers are thin shims with the same
 * leaf semantics as do_*_at() (dry-run/read-only guards, AT_SYMLINK_NOFOLLOW,
 * fake-super placeholder files); they never re-check the gate or re-resolve a
 * parent. */

int open_dir_secure(const char *dirname)
{
#ifdef AT_FDCWD
	extern int am_daemon, am_chrooted;
	int dfd;

	/* Authority gate, identical to the do_*_at() wrappers.  When hardened
	 * resolution isn't in effect, return -1 with errno cleared so the caller
	 * uses the full-path wrappers. */
	if (!secure_relpath_active()) {
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
		dfd = secure_relative_open(NULL, dirname, O_RDONLY | O_DIRECTORY, 0);
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
 * symlinks exactly as secure_relative_open() does; only the resolved dir fd is
 * kept (intermediate symlink-target fds are closed -- sound, since an open
 * dirfd needs no live parent). */
#if defined AT_FDCWD && defined O_NOFOLLOW && defined O_DIRECTORY
#define DPC_MAXDEPTH 64
static const char *dpc_anchor = (const char *)-2;
static int dpc_base = -1;			/* opened anchor dir (owned), or -1 */
static int dpc_fd[DPC_MAXDEPTH];		/* dpc_fd[i] = dir after components 0..i */
static char dpc_name[DPC_MAXDEPTH][256];	/* textual component names */
static int dpc_depth = 0;

void reset_dir_fd_cache(void)
{
	while (dpc_depth > 0)
		close(dpc_fd[--dpc_depth]);
	if (dpc_base >= 0)
		close(dpc_base);
	dpc_base = -1;
	dpc_anchor = (const char *)-2;
}

/* Resolve directory `dirpath` beneath `anchor` (NULL = cwd, else an absolute
 * trusted root), reusing the held ancestor stack.  Returns a BORROWED dirfd
 * owned by the cache (do NOT close), or -1 (errno preserved for a real open
 * error, errno==0 for an uncacheable path -- "..", too deep/long, or a relative
 * non-cwd anchor) so the caller can fall back to secure_relative_open(). */
static int dpc_dir_fd(const char *anchor, const char *dirpath)
{
	char copy[MAXPATHLEN];
	char *comps[DPC_MAXDEPTH];
	char *sv = NULL;
	int nc = 0, p, i;

	if (anchor && anchor[0] != '/') { errno = 0; return -1; }
	if (!dirpath)
		dirpath = "";
	if (dirpath[0] == '/') { errno = 0; return -1; }

	if (anchor != dpc_anchor || dpc_base < 0) {
		int fl;
		reset_dir_fd_cache();
		dpc_base = open_anchor_dirfd(anchor ? anchor : ".");
		if (dpc_base < 0)
			return -1;
		if ((fl = fcntl(dpc_base, F_GETFD)) >= 0)
			fcntl(dpc_base, F_SETFD, fl | FD_CLOEXEC);
		dpc_anchor = anchor;
	}

	if (strlcpy(copy, dirpath, sizeof copy) >= sizeof copy) { errno = ENAMETOOLONG; return -1; }
	for (char *c = strtok_r(copy, "/", &sv); c; c = strtok_r(NULL, "/", &sv)) {
		if (c[0] == '.' && c[1] == '\0')
			continue;					/* "." */
		if (c[0] == '.' && c[1] == '.' && c[2] == '\0') { errno = 0; return -1; }
		if (nc >= DPC_MAXDEPTH || strlen(c) >= sizeof dpc_name[0]) {
			/* Too deep / a too-long component to cache.  Release the held
			 * ancestor fds first so the caller's full-path fallback walk does
			 * not stack on top of them: a deep tree plus a low RLIMIT_NOFILE
			 * (e.g. OpenBSD's default 128) would otherwise exhaust descriptors
			 * (cache depth + walk depth). */
			reset_dir_fd_cache();
			errno = 0;
			return -1;
		}
		comps[nc++] = c;
	}

	/* Reuse the longest common prefix; drop the divergent tail. */
	for (p = 0; p < dpc_depth && p < nc && strcmp(dpc_name[p], comps[p]) == 0; p++)
		;
	while (dpc_depth > p)
		close(dpc_fd[--dpc_depth]);

	/* Descend the new tail, holding each resolved component. */
	for (i = p; i < nc; i++) {
		int afd = dpc_depth > 0 ? dpc_fd[dpc_depth-1] : dpc_base;
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
		strlcpy(dpc_name[dpc_depth], comps[i], sizeof dpc_name[0]);
		dpc_fd[dpc_depth++] = fd;
	}

	return nc > 0 ? dpc_fd[dpc_depth-1] : dpc_base;
}

/* Public entry for the sender (no secure_relpath_active gate: its send paths
 * confine unconditionally).  Borrowed fd; -1 => caller uses the full walk. */
int held_dir_path_fd(const char *anchor, const char *dirpath)
{
	return dpc_dir_fd(anchor, dirpath);
}

int get_dir_fd(const char *dirname)
{
	if (!secure_relpath_active()) { errno = 0; return -1; }
	return dpc_dir_fd(NULL, dirname);
}
#else
void reset_dir_fd_cache(void)
{
}
int held_dir_path_fd(const char *anchor, const char *dirpath)
{
	(void)anchor;
	(void)dirpath;
	errno = 0;
	return -1;
}
int get_dir_fd(const char *dirname)
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
 * a differently-nested dir, or when open_dir_secure() is gated off.  The dirfd
 * is opened once and cached.
 *
 * file->basename is NOT assumed to equal `path`'s leaf (a temp file has a
 * different basename), so the caller derives the leaf from `path`. */
int held_dfd_for(const char *path, const struct file_struct *file)
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
	return get_dir_fd(file ? file->dirname : NULL);
}

int do_unlink_atfd(int dfd, const char *name, int flags)
{
#ifdef AT_FDCWD
	if (dry_run) return 0;
	RETURN_ERROR_IF_RO_OR_LO;
	return unlinkat(dfd, name, flags);
#else
	(void)dfd; (void)name; (void)flags;
	errno = ENOSYS;
	return -1;
#endif
}

int do_mkdir_atfd(int dfd, const char *name, mode_t mode)
{
#ifdef AT_FDCWD
	if (dry_run) return 0;
	RETURN_ERROR_IF_RO_OR_LO;
	return mkdirat(dfd, name, mode);
#else
	(void)dfd; (void)name; (void)mode;
	errno = ENOSYS;
	return -1;
#endif
}

#ifdef HAVE_CHMOD
int do_chmod_atfd(int dfd, const char *name, mode_t mode)
{
#ifdef AT_FDCWD
	if (dry_run) return 0;
	RETURN_ERROR_IF_RO_OR_LO;
	/* Do not follow a final-component symlink (closes the leaf race; the
	 * held parent dfd already confines the ancestors).  A symlink-as-object
	 * (S_ISLNK(mode)) is still handled by the caller via the full-path
	 * do_chmod() lchmod/setattrlist code, exactly as do_chmod_at() does. */
	return do_fchmodat_nofollow(dfd, name, mode);
#else
	(void)dfd; (void)name; (void)mode;
	errno = ENOSYS;
	return -1;
#endif
}
#endif

int do_lchown_atfd(int dfd, const char *name, uid_t owner, gid_t group)
{
#ifdef AT_FDCWD
	if (dry_run) return 0;
	RETURN_ERROR_IF_RO_OR_LO;
	return fchownat(dfd, name, owner, group, AT_SYMLINK_NOFOLLOW);
#else
	(void)dfd; (void)name; (void)owner; (void)group;
	errno = ENOSYS;
	return -1;
#endif
}

/* Mode/owner on an already-open fd (no path, no symlink to follow): the
 * race-free way to set metadata on a cross-tree operator-path leaf that was
 * pinned with O_NOFOLLOW.  See set_file_attrs(). */
int do_fchown(int fd, uid_t owner, gid_t group)
{
	if (dry_run) return 0;
	RETURN_ERROR_IF_RO_OR_LO;
	return fchown(fd, owner, group);
}

#ifdef HAVE_CHMOD
int do_fchmod(int fd, mode_t mode)
{
	if (dry_run) return 0;
	RETURN_ERROR_IF_RO_OR_LO;
	return fchmod(fd, mode);
}
#endif

#ifdef HAVE_FUTIMENS
/* Set times on an already-open fd (the race-free counterpart for a pinned
 * cross-tree operator leaf -- see set_file_attrs()). */
int do_futimens(int fd, STRUCT_STAT *stp)
{
	struct timespec t[2];

	if (dry_run) return 0;
	RETURN_ERROR_IF_RO_OR_LO;
	t[0].tv_sec = stp->st_atime;
#ifdef ST_ATIME_NSEC
	t[0].tv_nsec = stp->ST_ATIME_NSEC;
#else
	t[0].tv_nsec = 0;
#endif
	t[1].tv_sec = stp->st_mtime;
#ifdef ST_MTIME_NSEC
	t[1].tv_nsec = stp->ST_MTIME_NSEC;
#else
	t[1].tv_nsec = 0;
#endif
	return futimens(fd, t);
}
#endif

#ifdef HAVE_UTIMENSAT
int do_utimensat_atfd(int dfd, const char *name, STRUCT_STAT *stp)
{
#ifdef AT_FDCWD
	struct timespec t[2];

	if (dry_run) return 0;
	RETURN_ERROR_IF_RO_OR_LO;

	t[0].tv_sec = stp->st_atime;
#ifdef ST_ATIME_NSEC
	t[0].tv_nsec = stp->ST_ATIME_NSEC;
#else
	t[0].tv_nsec = 0;
#endif
	t[1].tv_sec = stp->st_mtime;
#ifdef ST_MTIME_NSEC
	t[1].tv_nsec = stp->ST_MTIME_NSEC;
#else
	t[1].tv_nsec = 0;
#endif
	return utimensat(dfd, name, t, AT_SYMLINK_NOFOLLOW);
#else
	(void)dfd; (void)name; (void)stp;
	errno = ENOSYS;
	return -1;
#endif
}
#endif

int do_open_atfd(int dfd, const char *name, int flags, mode_t mode)
{
#ifdef AT_FDCWD
	if (flags != O_RDONLY) {
		RETURN_ERROR_IF(dry_run, 0);
		RETURN_ERROR_IF_RO_OR_LO;
	}
#ifdef O_NOATIME
	if (open_noatime)
		flags |= O_NOATIME;
#endif
	return openat(dfd, name, flags | O_NOFOLLOW | O_BINARY, mode);
#else
	(void)dfd; (void)name; (void)flags; (void)mode;
	errno = ENOSYS;
	return -1;
#endif
}

int do_symlink_atfd(const char *lnk, int dfd, const char *name)
{
#ifdef AT_FDCWD
	if (dry_run) return 0;
	RETURN_ERROR_IF_RO_OR_LO;

#if defined NO_SYMLINK_XATTRS || defined NO_SYMLINK_USER_XATTRS
	/* --fake-super: store the link target in a regular placeholder file,
	 * created with O_NOFOLLOW so a planted basename symlink can't redirect
	 * the write (mirrors do_symlink_at()). */
	if (am_root < 0) {
		int len = strlen(lnk);
		int ok;
		int fd = openat(dfd, name, O_WRONLY | O_CREAT | O_TRUNC | O_NOFOLLOW,
				S_IWUSR | S_IRUSR);
		if (fd < 0)
			return -1;
		ok = write(fd, lnk, len) == len;
		if (close(fd) < 0)
			ok = 0;
		return ok ? 0 : -1;
	}
#endif
	return symlinkat(lnk, dfd, name);
#else
	(void)lnk; (void)dfd; (void)name;
	errno = ENOSYS;
	return -1;
#endif
}

int do_mknod_atfd(int dfd, const char *name, mode_t mode, dev_t dev)
{
#ifdef AT_FDCWD
	if (dry_run) return 0;
	RETURN_ERROR_IF_RO_OR_LO;

	if (am_root < 0) {
		/* --fake-super: regular empty placeholder file (O_NOFOLLOW). */
		int fd = openat(dfd, name, O_WRONLY | O_CREAT | O_TRUNC | O_NOFOLLOW,
				S_IWUSR | S_IRUSR);
		if (fd < 0)
			return -1;
		return (close(fd) < 0) ? -1 : 0;
	}

	/* Try mknodat first; on failure retry race-safely with the type-
	 * specific primitive (see do_mknod()).  HAVE_MKNODAT, not HAVE_MKNOD:
	 * older Darwin has mknod() but not mknodat(), so keying off the former
	 * compiles a call that then fails to link (#161). */
#ifdef HAVE_MKNODAT
	if (mknodat(dfd, name, mode, dev) == 0)
		return 0;
#endif
#ifdef HAVE_MKFIFOAT
	if (S_ISFIFO(mode))
		return mkfifoat(dfd, name, mode);
#endif
	if (S_ISSOCK(mode)) {
		/* No dirfd-relative socket bind without /proc/self/fd; fail safe.
		 * (The generator routes sockets to do_mknod_at(), not here.) */
		errno = EOPNOTSUPP;
		return -1;
	}
#ifdef HAVE_MKNODAT
	return -1;	/* mknodat()'s errno (regular/device node) */
#else
	/* Must match the guard above: reporting "mknodat()'s errno" where the
	 * call was never compiled would return a stale errno. */
	(void)dev;
	errno = ENOSYS;
	return -1;
#endif
#else
	(void)dfd; (void)name; (void)mode; (void)dev;
	errno = ENOSYS;
	return -1;
#endif
}

int do_rename_atfd(int old_dfd, const char *old_name, int new_dfd, const char *new_name)
{
#ifdef AT_FDCWD
	if (dry_run) return 0;
	RETURN_ERROR_IF_RO_OR_LO;
	return renameat(old_dfd, old_name, new_dfd, new_name);
#else
	(void)old_dfd; (void)old_name; (void)new_dfd; (void)new_name;
	errno = ENOSYS;
	return -1;
#endif
}

#if defined HAVE_LINK || defined HAVE_LINKAT
int do_link_atfd(int old_dfd, const char *old_name, int new_dfd, const char *new_name, int flags)
{
#if defined AT_FDCWD && defined HAVE_LINKAT
	if (dry_run) return 0;
	RETURN_ERROR_IF_RO_OR_LO;
	return linkat(old_dfd, old_name, new_dfd, new_name, flags);
#else
	(void)old_dfd; (void)old_name; (void)new_dfd; (void)new_name; (void)flags;
	errno = ENOSYS;
	return -1;
#endif
}
#endif

int do_lstat_atfd(int dfd, const char *name, STRUCT_STAT *st)
{
#ifdef AT_FDCWD
# ifdef SUPPORT_LINKS
	return fstatat(dfd, name, st, AT_SYMLINK_NOFOLLOW);
# else
	return fstatat(dfd, name, st, 0);
# endif
#else
	(void)dfd; (void)name; (void)st;
	errno = ENOSYS;
	return -1;
#endif
}

int do_stat_atfd(int dfd, const char *name, STRUCT_STAT *st)
{
#ifdef AT_FDCWD
	return fstatat(dfd, name, st, 0);
#else
	(void)dfd; (void)name; (void)st;
	errno = ENOSYS;
	return -1;
#endif
}

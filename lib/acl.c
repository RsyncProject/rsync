/*
 * POSIX ACL get/set/delete via the generic xattr syscalls.
 *
 * POSIX ACLs are stored by the kernel as the "system.posix_acl_access" and
 * "system.posix_acl_default" extended attributes, in a fixed little-endian
 * wire format (see include/acl_ea.h in the acl package).  By serializing that
 * format ourselves and using fgetxattr/fsetxattr on a held O_NOFOLLOW fd -- or
 * getxattrat/setxattrat(AT_SYMLINK_NOFOLLOW) on a dirfd+leaf -- we get a
 * symlink-race-safe ACL primitive that also covers the *default* ACL, which
 * libacl's fd API (acl_get_fd/acl_set_fd, access-only) cannot.
 *
 * This file knows nothing about rsync's globals or its internal ACL form: it
 * speaks a neutral (tag, perm, id) entry array, which makes it directly
 * comparable against the system libacl in the t_acl unit test.
 *
 * Copyright (C) 2026 Wayne Davison & the rsync project
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
#include "acl.h"

#ifdef SUPPORT_ACL_FD

#include <errno.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <fcntl.h>		/* AT_SYMLINK_NOFOLLOW */

#if defined HAVE_SYS_XATTR_H
#include <sys/xattr.h>
#elif defined HAVE_ATTR_XATTR_H
#include <attr/xattr.h>
#endif

#ifdef HAVE_XATTRAT_SYSCALLS
#include <sys/syscall.h>
/* Self-contained copy of the kernel's struct xattr_args (stable ABI: an
 * 8-byte-aligned u64 pointer, then two u32s).  Defined locally to avoid
 * pulling <linux/xattr.h>, whose XATTR_* macros clash with <sys/xattr.h>. */
struct rsync_xattr_args {
	uint64_t value __attribute__((aligned(8)));
	uint32_t size;
	uint32_t flags;
};
#endif

/* Linux 2.4 didn't have a distinct ENOATTR. */
#ifndef ENOATTR
#define ENOATTR ENODATA
#endif

#define ACL_XATTR_ACCESS  "system.posix_acl_access"
#define ACL_XATTR_DEFAULT "system.posix_acl_default"

/* On-disk layout: a 4-byte LE version header followed by 8-byte LE entries. */
#define ACL_EA_VERSION 0x0002
#define ACL_EA_HDR_LEN 4
#define ACL_EA_ENT_LEN 8

/* === little-endian (de)serialization (host-endianness independent) === */

static void put_le16(unsigned char *p, uint16_t v)
{
	p[0] = (unsigned char)(v & 0xff);
	p[1] = (unsigned char)((v >> 8) & 0xff);
}

static void put_le32(unsigned char *p, uint32_t v)
{
	p[0] = (unsigned char)(v & 0xff);
	p[1] = (unsigned char)((v >> 8) & 0xff);
	p[2] = (unsigned char)((v >> 16) & 0xff);
	p[3] = (unsigned char)((v >> 24) & 0xff);
}

static uint16_t get_le16(const unsigned char *p)
{
	return (uint16_t)(p[0] | (p[1] << 8));
}

static uint32_t get_le32(const unsigned char *p)
{
	return (uint32_t)p[0] | ((uint32_t)p[1] << 8)
	     | ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}

static int is_named_tag(uint16_t tag)
{
	return tag == RACL_USER || tag == RACL_GROUP;
}

/* Canonical order: tag ascending, then id ascending within a tag.  This is
 * the order libacl's __acl_reorder_obj_p() produces and what the kernel's
 * validator expects (USER_OBJ, USER*, GROUP_OBJ, GROUP*, MASK, OTHER). */
static int ent_compare(const void *a, const void *b)
{
	const rsync_acl_ent *x = a, *y = b;
	if (x->tag != y->tag)
		return x->tag < y->tag ? -1 : 1;
	if (x->id != y->id)
		return x->id < y->id ? -1 : 1;
	return 0;
}

/* Serialize entries into a freshly-malloc'd xattr buffer (canonical order). */
static unsigned char *acl_to_xattr(const rsync_acl_ent *ents, int count, size_t *len_out)
{
	size_t len = ACL_EA_HDR_LEN + (size_t)count * ACL_EA_ENT_LEN;
	unsigned char *buf = malloc(len);
	rsync_acl_ent *sorted = NULL;
	unsigned char *p;
	int i;

	if (!buf)
		return NULL;
	if (count > 1) {
		sorted = malloc((size_t)count * sizeof sorted[0]);
		if (!sorted) {
			free(buf);
			return NULL;
		}
		memcpy(sorted, ents, (size_t)count * sizeof sorted[0]);
		qsort(sorted, count, sizeof sorted[0], ent_compare);
		ents = sorted;
	}

	put_le32(buf, ACL_EA_VERSION);
	p = buf + ACL_EA_HDR_LEN;
	for (i = 0; i < count; i++, p += ACL_EA_ENT_LEN) {
		put_le16(p, ents[i].tag);
		put_le16(p + 2, ents[i].perm);
		put_le32(p + 4, is_named_tag(ents[i].tag) ? ents[i].id : RACL_UNDEFINED_ID);
	}

	if (sorted)
		free(sorted);
	*len_out = len;
	return buf;
}

/* Parse an xattr buffer into a malloc'd entry array (canonical order). */
static int xattr_to_acl(const unsigned char *buf, size_t len,
			rsync_acl_ent **out, int *count_out)
{
	rsync_acl_ent *ents;
	const unsigned char *p;
	int n, i;

	if (len < ACL_EA_HDR_LEN || (len - ACL_EA_HDR_LEN) % ACL_EA_ENT_LEN != 0
	 || get_le32(buf) != ACL_EA_VERSION) {
		errno = EINVAL;
		return -1;
	}
	n = (int)((len - ACL_EA_HDR_LEN) / ACL_EA_ENT_LEN);

	ents = n ? malloc((size_t)n * sizeof ents[0]) : NULL;
	if (n && !ents)
		return -1;
	p = buf + ACL_EA_HDR_LEN;
	for (i = 0; i < n; i++, p += ACL_EA_ENT_LEN) {
		ents[i].tag = get_le16(p);
		ents[i].perm = get_le16(p + 2);
		ents[i].id = is_named_tag(ents[i].tag) ? get_le32(p + 4) : RACL_UNDEFINED_ID;
	}
	if (n > 1)
		qsort(ents, n, sizeof ents[0], ent_compare);

	*out = ents;
	*count_out = n;
	return 0;
}

/* === syscall dispatchers (fd-variant vs at-variant) === */

/* Pre-6.13 fallback for the dirfd+leaf at-variants: address the leaf as
 * /proc/self/fd/<dirfd>/<leaf> and use the l*xattr (no-follow-leaf) calls.  The
 * /proc/self/fd/<dirfd> magic symlink resolves to the pinned parent inode -- a
 * raced parent symlink cannot redirect it -- and l*xattr does not follow a raced
 * leaf symlink, so this is race-safe without the Linux 6.13 *xattrat syscalls, as
 * long as procfs is mounted.  (`leaf` is a single component, <= NAME_MAX.)
 * Returns 0 and fills `buf`, or -1 with ENAMETOOLONG. */
static int proc_fd_leaf_path(char *buf, size_t buflen, int dirfd, const char *leaf)
{
	int n = snprintf(buf, buflen, "/proc/self/fd/%d/%s", dirfd, leaf);
	if (n < 0 || (size_t)n >= buflen) {
		errno = ENAMETOOLONG;
		return -1;
	}
	return 0;
}

static ssize_t do_getxattr(int fd, int dirfd, const char *leaf,
			   const char *name, void *val, size_t size)
{
	char p[MAXPATHLEN];

	if (fd >= 0)
		return fgetxattr(fd, name, val, size);
#ifdef HAVE_XATTRAT_SYSCALLS
	{
		struct rsync_xattr_args args;
		ssize_t ret;
		args.value = (uint64_t)(uintptr_t)val;
		args.size = (uint32_t)size;
		args.flags = 0;
		ret = syscall(SYS_getxattrat, dirfd, leaf, AT_SYMLINK_NOFOLLOW,
			      name, &args, sizeof args);
		if (ret != -1 || errno != ENOSYS)
			return ret;
		/* ENOSYS: kernel < 6.13 -- fall through to the /proc compat. */
	}
#endif
	if (proc_fd_leaf_path(p, sizeof p, dirfd, leaf) < 0)
		return -1;
	return lgetxattr(p, name, val, size);
}

static int do_setxattr(int fd, int dirfd, const char *leaf,
		       const char *name, const void *val, size_t size)
{
	char p[MAXPATHLEN];

	if (fd >= 0)
		return fsetxattr(fd, name, val, size, 0);
#ifdef HAVE_XATTRAT_SYSCALLS
	{
		struct rsync_xattr_args args;
		int ret;
		args.value = (uint64_t)(uintptr_t)val;
		args.size = (uint32_t)size;
		args.flags = 0;	/* replace */
		ret = syscall(SYS_setxattrat, dirfd, leaf, AT_SYMLINK_NOFOLLOW,
			      name, &args, sizeof args);
		if (ret != -1 || errno != ENOSYS)
			return ret;
	}
#endif
	if (proc_fd_leaf_path(p, sizeof p, dirfd, leaf) < 0)
		return -1;
	return lsetxattr(p, name, val, size, 0);
}

static int do_removexattr(int fd, int dirfd, const char *leaf, const char *name)
{
	char p[MAXPATHLEN];

	if (fd >= 0)
		return fremovexattr(fd, name);
#ifdef HAVE_XATTRAT_SYSCALLS
	{
		int ret = syscall(SYS_removexattrat, dirfd, leaf, AT_SYMLINK_NOFOLLOW, name);
		if (ret != -1 || errno != ENOSYS)
			return ret;
	}
#endif
	if (proc_fd_leaf_path(p, sizeof p, dirfd, leaf) < 0)
		return -1;
	return lremovexattr(p, name);
}

/* Read the whole named xattr into a malloc'd buffer, growing as needed. */
static int read_full_xattr(int fd, int dirfd, const char *leaf,
			   const char *name, unsigned char **buf_out, size_t *len_out)
{
	unsigned char *buf = NULL;
	size_t size = 0;
	int tries;

	for (tries = 0; tries < 8; tries++) {
		ssize_t n = do_getxattr(fd, dirfd, leaf, name, size ? buf : NULL, size);
		if (n >= 0) {
			if (size == 0) {
				/* First call just learned the length. */
				size = n ? (size_t)n : 1;
				buf = malloc(size);
				if (!buf)
					return -1;
				continue;
			}
			*buf_out = buf;
			*len_out = (size_t)n;
			return 0;
		}
		if (errno == ERANGE) {	/* grew under us: re-probe the size */
			if (buf)
				free(buf);
			buf = NULL;
			size = 0;
			continue;
		}
		if (buf)
			free(buf);
		return -1;	/* ENODATA / EOPNOTSUPP / ENOSYS / ... in errno */
	}
	if (buf)
		free(buf);
	errno = ERANGE;
	return -1;
}

/* === public API === */

static int acl_get_common(int fd, int dirfd, const char *leaf,
			  int want_default, rsync_acl_ent **entries, int *count)
{
	const char *name = want_default ? ACL_XATTR_DEFAULT : ACL_XATTR_ACCESS;
	unsigned char *buf;
	size_t len;
	int rc;

	*entries = NULL;
	*count = 0;

	if (read_full_xattr(fd, dirfd, leaf, name, &buf, &len) < 0) {
		if (errno == ENODATA || errno == ENOATTR)
			return 0;	/* no explicit ACL present */
		return -1;		/* EOPNOTSUPP / ENOSYS / real error */
	}

	rc = xattr_to_acl(buf, len, entries, count);
	free(buf);
	return rc;
}

int xacl_get_fd(int fd, int want_default, rsync_acl_ent **entries, int *count)
{
	return acl_get_common(fd, -1, NULL, want_default, entries, count);
}

int xacl_get_at(int dirfd, const char *leaf, int want_default,
		rsync_acl_ent **entries, int *count)
{
	return acl_get_common(-1, dirfd, leaf, want_default, entries, count);
}

static int acl_set_common(int fd, int dirfd, const char *leaf,
			  int want_default, const rsync_acl_ent *ents, int count)
{
	const char *name = want_default ? ACL_XATTR_DEFAULT : ACL_XATTR_ACCESS;
	unsigned char *buf;
	size_t len;
	int rc, save_errno;

	buf = acl_to_xattr(ents, count, &len);
	if (!buf) {
		errno = ENOMEM;
		return -1;
	}
	rc = do_setxattr(fd, dirfd, leaf, name, buf, len);
	save_errno = errno;
	free(buf);
	errno = save_errno;
	return rc < 0 ? -1 : 0;
}

int xacl_set_fd(int fd, int want_default, const rsync_acl_ent *ents, int count)
{
	return acl_set_common(fd, -1, NULL, want_default, ents, count);
}

int xacl_set_at(int dirfd, const char *leaf, int want_default,
		const rsync_acl_ent *ents, int count)
{
	return acl_set_common(-1, dirfd, leaf, want_default, ents, count);
}

static int acl_del_default_common(int fd, int dirfd, const char *leaf)
{
	if (do_removexattr(fd, dirfd, leaf, ACL_XATTR_DEFAULT) < 0) {
		if (errno == ENODATA || errno == ENOATTR)
			return 0;	/* already absent: success, like acl_delete_def_file */
		return -1;
	}
	return 0;
}

int xacl_del_default_fd(int fd)
{
	return acl_del_default_common(fd, -1, NULL);
}

int xacl_del_default_at(int dirfd, const char *leaf)
{
	return acl_del_default_common(-1, dirfd, leaf);
}

/* True iff /proc/self/fd magic symlinks are usable, so the dirfd+leaf at-variants
 * work race-safely via the /proc compat on a pre-6.13 kernel. */
static int proc_self_fd_usable(void)
{
	int dfd = open(".", O_RDONLY | O_DIRECTORY | O_CLOEXEC);
	char p[64];
	int usable = 0;

	if (dfd < 0)
		return 0;
	if (snprintf(p, sizeof p, "/proc/self/fd/%d/.", dfd) < (int)sizeof p) {
		/* The probe attr is absent; the path resolving (any errno but
		 * ENOENT/ENOTDIR -- e.g. ENODATA/ENOTSUP/EACCES) means procfs gives us
		 * the magic fd-symlink we need. */
		errno = 0;
		lgetxattr(p, "user.rsync_acl_probe", NULL, 0);
		usable = !(errno == ENOENT || errno == ENOTDIR);
	}
	close(dfd);
	return usable;
}

int xacl_at_available(void)
{
	static int avail = -1;

	if (avail < 0) {
#ifdef HAVE_XATTRAT_SYSCALLS
		/* Probe the *xattrat syscall directly (not via do_getxattr's /proc
		 * fallback): any errno other than ENOSYS means it is present (6.13+). */
		struct rsync_xattr_args args;
		args.value = 0;
		args.size = 0;
		args.flags = 0;
		errno = 0;
		syscall(SYS_getxattrat, AT_FDCWD, ".", AT_SYMLINK_NOFOLLOW,
			"user.rsync_acl_probe", &args, sizeof args);
		if (errno != ENOSYS) {
			avail = 1;
			return avail;
		}
#endif
		/* No *xattrat syscalls (pre-6.13, or a kernel built without them): the dirfd+leaf ACL ops
		 * are still race-safe via /proc/self/fd if procfs is mounted, closing
		 * the parent-symlink-race gap that otherwise forces the path-based set. */
		avail = proc_self_fd_usable();
	}
	return avail;
}

#endif /* SUPPORT_ACL_FD */

/*
 * POSIX ACL get/set/delete via the generic xattr syscalls, addressing the
 * kernel "system.posix_acl_{access,default}" attributes directly so that the
 * operation can be confined to a held O_NOFOLLOW fd (fsetxattr) or a
 * dirfd+leaf with AT_SYMLINK_NOFOLLOW (setxattrat).  This replaces the path-
 * based libacl acl_*_file() calls on Linux, where those would re-resolve the
 * path and could be redirected by a parent-component symlink race.
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

#ifdef SUPPORT_ACL_FD

#include <stdint.h>

/* A single logical POSIX ACL entry in host-native form.  The tag values are
 * the stable kernel ABI numbers (== the libacl ACL_* constants), so they map
 * straight onto the on-disk e_tag without translation. */
typedef struct {
	uint16_t tag;	/* RACL_USER_OBJ / USER / GROUP_OBJ / GROUP / MASK / OTHER */
	uint16_t perm;	/* permission bits: read=4, write=2, execute=1 */
	uint32_t id;	/* uid/gid for USER/GROUP entries; RACL_UNDEFINED_ID otherwise */
} rsync_acl_ent;

#define RACL_USER_OBJ	0x01
#define RACL_USER	0x02
#define RACL_GROUP_OBJ	0x04
#define RACL_GROUP	0x08
#define RACL_MASK	0x10
#define RACL_OTHER	0x20

#define RACL_UNDEFINED_ID ((uint32_t)-1)

/* Read the access (want_default==0) or default (want_default!=0) ACL.
 *
 * On success returns 0 and sets *entries to a malloc()ed array of *count
 * entries (the caller frees it with free(); *entries may be NULL when
 * *count==0, which means "no explicit ACL present" -- e.g. ENODATA).
 *
 * On failure returns -1 with errno set.  Callers distinguish:
 *   ENOTSUP/EOPNOTSUPP - this filesystem has no ACL support (may differ per fs)
 *   ENOSYS            - the at-variant syscalls are unavailable on this kernel
 * The fd-variant operates on a held, already-NOFOLLOW-opened descriptor.  The
 * at-variant resolves leaf relative to dirfd and never follows a leaf symlink. */
int xacl_get_fd(int fd, int want_default, rsync_acl_ent **entries, int *count);
int xacl_get_at(int dirfd, const char *leaf, int want_default, rsync_acl_ent **entries, int *count);

/* Write the given entries as the access/default ACL.  The entries are emitted
 * in canonical order; the kernel validates them (a malformed set -> EINVAL). */
int xacl_set_fd(int fd, int want_default, const rsync_acl_ent *entries, int count);
int xacl_set_at(int dirfd, const char *leaf, int want_default, const rsync_acl_ent *entries, int count);

/* Delete a directory's default ACL.  A missing default ACL is success. */
int xacl_del_default_fd(int fd);
int xacl_del_default_at(int dirfd, const char *leaf);

/* Cached runtime probe: are the *xattrat syscalls usable on this kernel?
 * Returns 0 when they are absent (so callers can fall back) or unbuilt. */
int xacl_at_available(void);

#endif /* SUPPORT_ACL_FD */

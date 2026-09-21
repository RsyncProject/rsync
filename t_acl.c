/*
 * Unit test for lib/acl.c.
 *
 * Validates the fd- and at-based POSIX ACL get/set/delete in lib/acl.c against
 * the system libacl, used here as an oracle: we set an ACL via one and read it
 * back via the other (both directions), round-trip through lib/acl.c, and check
 * the default-ACL and delete paths.  We deliberately do NOT try to reproduce
 * libacl's symlink-following races -- we only compare functional behaviour.
 *
 * Not linked into rsync itself.  Exits 0 if all checks pass, 1 on any failure,
 * 77 to skip (built without SUPPORT_ACL_FD, no libacl, or a scratch filesystem
 * without ACL support).
 *
 * This program is free software; you can redistribute it and/or modify
 * it under the terms of the GNU General Public License version 2 as
 * published by the Free Software Foundation.
 */

#include "rsync.h"
#include "lib/acl.h"

#include <stdio.h>

#ifndef SUPPORT_ACL_FD

int main(int argc, char *argv[])
{
	(void)argc;
	(void)argv;
	fprintf(stderr, "t_acl: built without SUPPORT_ACL_FD -- skipping\n");
	return 77;
}

#else

#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <fcntl.h>
#include <errno.h>
#include <sys/types.h>
#include <sys/stat.h>
#include <sys/acl.h>
#ifdef HAVE_ACL_LIBACL_H
#include <acl/libacl.h>		/* acl_get_perm() */
#endif

#define MAX_ENT 16

static int errs = 0;
static const char *scratch;

static void ok(int cond, const char *label)
{
	if (cond)
		fprintf(stderr, "OK   %s\n", label);
	else {
		fprintf(stderr, "FAIL %s\n", label);
		errs++;
	}
}

static void dump_entries(const char *pfx, const rsync_acl_ent *e, int n)
{
	int i;
	for (i = 0; i < n; i++)
		fprintf(stderr, "    %s tag=0x%02x perm=%o id=%u\n",
			pfx, e[i].tag, e[i].perm,
			e[i].id == RACL_UNDEFINED_ID ? (unsigned)-1 : e[i].id);
}

static int ent_cmp(const void *a, const void *b)
{
	const rsync_acl_ent *x = a, *y = b;
	if (x->tag != y->tag)
		return x->tag < y->tag ? -1 : 1;
	if (x->id != y->id)
		return x->id < y->id ? -1 : 1;
	return 0;
}

static void canon(rsync_acl_ent *e, int n)
{
	if (n > 1)
		qsort(e, n, sizeof e[0], ent_cmp);
}

static int entries_equal(rsync_acl_ent *a, int na, rsync_acl_ent *b, int nb)
{
	int i;
	canon(a, na);
	canon(b, nb);
	if (na != nb)
		return 0;
	for (i = 0; i < na; i++) {
		if (a[i].tag != b[i].tag || a[i].perm != b[i].perm)
			return 0;
		if ((a[i].tag == RACL_USER || a[i].tag == RACL_GROUP)
		 && a[i].id != b[i].id)
			return 0;
	}
	return 1;
}

/* === libacl oracle helpers === */

static uint16_t racl_from_tag(acl_tag_t tag)
{
	switch (tag) {
	case ACL_USER_OBJ: return RACL_USER_OBJ;
	case ACL_USER: return RACL_USER;
	case ACL_GROUP_OBJ: return RACL_GROUP_OBJ;
	case ACL_GROUP: return RACL_GROUP;
	case ACL_MASK: return RACL_MASK;
	case ACL_OTHER: return RACL_OTHER;
	}
	return 0;
}

static acl_tag_t tag_from_racl(uint16_t tag)
{
	switch (tag) {
	case RACL_USER_OBJ: return ACL_USER_OBJ;
	case RACL_USER: return ACL_USER;
	case RACL_GROUP_OBJ: return ACL_GROUP_OBJ;
	case RACL_GROUP: return ACL_GROUP;
	case RACL_MASK: return ACL_MASK;
	case RACL_OTHER: return ACL_OTHER;
	}
	return 0;
}

/* Convert a libacl acl_t to our neutral entry array.  Returns count or -1. */
static int libacl_to_entries(acl_t acl, rsync_acl_ent *out, int max)
{
	acl_entry_t e;
	int n = 0, r;

	for (r = acl_get_entry(acl, ACL_FIRST_ENTRY, &e); r == 1;
	     r = acl_get_entry(acl, ACL_NEXT_ENTRY, &e)) {
		acl_tag_t tag;
		acl_permset_t ps;
		uint16_t perm = 0;

		if (n >= max)
			return -1;
		if (acl_get_tag_type(e, &tag) != 0 || acl_get_permset(e, &ps) != 0)
			return -1;
		if (acl_get_perm(ps, ACL_READ) > 0)
			perm |= 4;
		if (acl_get_perm(ps, ACL_WRITE) > 0)
			perm |= 2;
		if (acl_get_perm(ps, ACL_EXECUTE) > 0)
			perm |= 1;
		out[n].tag = racl_from_tag(tag);
		out[n].perm = perm;
		out[n].id = RACL_UNDEFINED_ID;
		if (tag == ACL_USER || tag == ACL_GROUP) {
			void *q = acl_get_qualifier(e);
			if (q) {
				out[n].id = *(id_t *)q;
				acl_free(q);
			}
		}
		n++;
	}
	if (r < 0)
		return -1;
	canon(out, n);
	return n;
}

/* Build a libacl acl_t from our neutral entries. */
static acl_t entries_to_libacl(const rsync_acl_ent *ents, int n)
{
	acl_t acl = acl_init(n);
	int i;

	if (!acl)
		return NULL;
	for (i = 0; i < n; i++) {
		acl_entry_t e;
		acl_permset_t ps;
		if (acl_create_entry(&acl, &e) != 0)
			goto fail;
		if (acl_set_tag_type(e, tag_from_racl(ents[i].tag)) != 0)
			goto fail;
		if (acl_get_permset(e, &ps) != 0 || acl_clear_perms(ps) != 0)
			goto fail;
		if ((ents[i].perm & 4) && acl_add_perm(ps, ACL_READ) != 0)
			goto fail;
		if ((ents[i].perm & 2) && acl_add_perm(ps, ACL_WRITE) != 0)
			goto fail;
		if ((ents[i].perm & 1) && acl_add_perm(ps, ACL_EXECUTE) != 0)
			goto fail;
		if (acl_set_permset(e, ps) != 0)
			goto fail;
		if (ents[i].tag == RACL_USER || ents[i].tag == RACL_GROUP) {
			id_t id = ents[i].id;
			if (acl_set_qualifier(e, &id) != 0)
				goto fail;
		}
	}
	return acl;
  fail:
	acl_free(acl);
	return NULL;
}

static int libacl_get(const char *path, acl_type_t type, rsync_acl_ent *out, int max)
{
	acl_t acl = acl_get_file(path, type);
	int n;
	if (!acl)
		return -1;
	n = libacl_to_entries(acl, out, max);
	acl_free(acl);
	return n;
}

static int libacl_set(const char *path, acl_type_t type, const rsync_acl_ent *ents, int n)
{
	acl_t acl = entries_to_libacl(ents, n);
	int rc;
	if (!acl)
		return -1;
	rc = acl_set_file(path, type, acl);
	acl_free(acl);
	return rc;
}

/* === scratch helpers === */

static char *acl_path(const char *name)
{
	static char buf[4096];
	snprintf(buf, sizeof buf, "%s/%s", scratch, name);
	return buf;
}

static int acl_mkfile(const char *name)
{
	char *p = acl_path(name);
	int fd = open(p, O_WRONLY | O_CREAT | O_TRUNC, 0644);
	if (fd >= 0)
		close(fd);
	return fd < 0 ? -1 : 0;
}

static int acl_mkdir(const char *name)
{
	return mkdir(acl_path(name), 0755);
}

/* Open a held fd the way set_file_attrs does (REG/DIR, NOFOLLOW). */
static int open_held(const char *name)
{
	return open(acl_path(name), O_RDONLY | O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC);
}

/* === comparison tests for one ACL shape === */

static void cmp_via_fd(const char *name, int want_default,
		       const rsync_acl_ent *ents, int n, const char *tag)
{
	char label[256];
	char *path = acl_path(name);
	acl_type_t type = want_default ? ACL_TYPE_DEFAULT : ACL_TYPE_ACCESS;
	rsync_acl_ent got[MAX_ENT], oracle[MAX_ENT];
	rsync_acl_ent *lib_ents = NULL;
	int gc = 0, oc, fd;

	/* 1. set via lib (fd) -> read via libacl */
	fd = open_held(name);
	snprintf(label, sizeof label, "%s: open held fd", tag);
	ok(fd >= 0, label);
	if (fd < 0)
		return;
	snprintf(label, sizeof label, "%s: xacl_set_fd", tag);
	ok(xacl_set_fd(fd, want_default, ents, n) == 0, label);
	oc = libacl_get(path, type, oracle, MAX_ENT);
	memcpy(got, ents, n * sizeof ents[0]);
	snprintf(label, sizeof label, "%s: libacl reads back what xacl_set_fd wrote", tag);
	if (!entries_equal(got, n, oracle, oc)) {
		dump_entries("set ", got, n);
		dump_entries("got ", oracle, oc < 0 ? 0 : oc);
	}
	ok(oc == n && entries_equal(got, n, oracle, oc), label);

	/* 2. set via libacl -> read via lib (fd) */
	snprintf(label, sizeof label, "%s: libacl_set", tag);
	ok(libacl_set(path, type, ents, n) == 0, label);
	snprintf(label, sizeof label, "%s: xacl_get_fd reads back what libacl wrote", tag);
	if (xacl_get_fd(fd, want_default, &lib_ents, &gc) == 0) {
		memcpy(got, ents, n * sizeof ents[0]);
		if (!entries_equal(got, n, lib_ents, gc)) {
			dump_entries("set ", got, n);
			dump_entries("got ", lib_ents, gc);
		}
		ok(gc == n && entries_equal(got, n, lib_ents, gc), label);
	} else
		ok(0, label);
	if (lib_ents)
		free(lib_ents);
	lib_ents = NULL;

	/* 3. round-trip lib set -> lib get */
	snprintf(label, sizeof label, "%s: xacl_set_fd/xacl_get_fd round-trip", tag);
	if (xacl_set_fd(fd, want_default, ents, n) == 0
	 && xacl_get_fd(fd, want_default, &lib_ents, &gc) == 0) {
		memcpy(got, ents, n * sizeof ents[0]);
		ok(gc == n && entries_equal(got, n, lib_ents, gc), label);
	} else
		ok(0, label);
	if (lib_ents)
		free(lib_ents);

	close(fd);
}

static void cmp_via_at(const char *name, int want_default,
		       const rsync_acl_ent *ents, int n, const char *tag)
{
	char label[256];
	char *path = acl_path(name);
	acl_type_t type = want_default ? ACL_TYPE_DEFAULT : ACL_TYPE_ACCESS;
	rsync_acl_ent got[MAX_ENT], oracle[MAX_ENT];
	rsync_acl_ent *lib_ents = NULL;
	int gc = 0, oc, dirfd;

	dirfd = open(scratch, O_RDONLY | O_DIRECTORY | O_CLOEXEC);
	snprintf(label, sizeof label, "%s: open scratch dirfd", tag);
	ok(dirfd >= 0, label);
	if (dirfd < 0)
		return;

	snprintf(label, sizeof label, "%s: xacl_set_at", tag);
	ok(xacl_set_at(dirfd, name, want_default, ents, n) == 0, label);
	oc = libacl_get(path, type, oracle, MAX_ENT);
	memcpy(got, ents, n * sizeof ents[0]);
	snprintf(label, sizeof label, "%s: libacl reads back what xacl_set_at wrote", tag);
	ok(oc == n && entries_equal(got, n, oracle, oc), label);

	snprintf(label, sizeof label, "%s: libacl_set", tag);
	ok(libacl_set(path, type, ents, n) == 0, label);
	snprintf(label, sizeof label, "%s: xacl_get_at reads back what libacl wrote", tag);
	if (xacl_get_at(dirfd, name, want_default, &lib_ents, &gc) == 0) {
		memcpy(got, ents, n * sizeof ents[0]);
		ok(gc == n && entries_equal(got, n, lib_ents, gc), label);
	} else
		ok(0, label);
	if (lib_ents)
		free(lib_ents);

	close(dirfd);
}

/* === scenarios === */

#define U(p)    { RACL_USER_OBJ, p, RACL_UNDEFINED_ID }
#define G(p)    { RACL_GROUP_OBJ, p, RACL_UNDEFINED_ID }
#define M(p)    { RACL_MASK, p, RACL_UNDEFINED_ID }
#define O(p)    { RACL_OTHER, p, RACL_UNDEFINED_ID }
#define NU(i,p) { RACL_USER, p, i }
#define NG(i,p) { RACL_GROUP, p, i }

static const rsync_acl_ent A1[] = { U(6), NU(12345,4), G(4), M(6), O(4) };
static const rsync_acl_ent A2[] = { U(7), NU(1000,6), NU(2000,5), G(5), NG(100,4), NG(200,1), M(7), O(0) };
static const rsync_acl_ent A3[] = { U(7), NU(4000000000U,5), G(5), M(7), O(4) };
static const rsync_acl_ent A4[] = { U(7), NU(0,0), G(0), M(4), O(0) };

static const rsync_acl_ent D1[] = { U(7), G(5), O(5) };
static const rsync_acl_ent D2[] = { U(7), NU(1000,6), G(5), NG(100,4), M(6), O(0) };

#define NELEM(a) ((int)(sizeof (a) / sizeof (a)[0]))

static void run_access(const char *base, const rsync_acl_ent *e, int n, const char *tag)
{
	char fdname[128], atname[128];
	char fdtag[160], attag[160];

	snprintf(fdname, sizeof fdname, "%s_fd", base);
	snprintf(atname, sizeof atname, "%s_at", base);
	snprintf(fdtag, sizeof fdtag, "access %s [fd]", tag);
	snprintf(attag, sizeof attag, "access %s [at]", tag);

	if (acl_mkfile(fdname) == 0)
		cmp_via_fd(fdname, 0, e, n, fdtag);
	if (xacl_at_available()) {
		if (acl_mkfile(atname) == 0)
			cmp_via_at(atname, 0, e, n, attag);
	}
}

static void run_default(const char *base, const rsync_acl_ent *e, int n, const char *tag)
{
	char fdname[128], atname[128];
	char fdtag[160], attag[160];

	snprintf(fdname, sizeof fdname, "%s_fd", base);
	snprintf(atname, sizeof atname, "%s_at", base);
	snprintf(fdtag, sizeof fdtag, "default %s [fd]", tag);
	snprintf(attag, sizeof attag, "default %s [at]", tag);

	if (acl_mkdir(fdname) == 0)
		cmp_via_fd(fdname, 1, e, n, fdtag);
	if (xacl_at_available()) {
		if (acl_mkdir(atname) == 0)
			cmp_via_at(atname, 1, e, n, attag);
	}
}

/* delete of a default ACL + the "no explicit ACL" / errno behaviour */
static void run_misc(void)
{
	rsync_acl_ent *ents = NULL;
	int n = 0, fd;
	char *p;

	/* default-ACL delete */
	if (acl_mkdir("deldir") == 0) {
		fd = open_held("deldir");
		ok(fd >= 0, "misc: open deldir fd");
		if (fd >= 0) {
			ok(xacl_set_fd(fd, 1, D2, NELEM(D2)) == 0, "misc: set default to delete");
			ok(xacl_del_default_fd(fd) == 0, "misc: xacl_del_default_fd");
			/* libacl returns an empty (0-entry) default ACL after delete */
			{
				acl_t a = acl_get_file(acl_path("deldir"), ACL_TYPE_DEFAULT);
				int cnt = -1;
				if (a) {
					acl_entry_t e;
					cnt = acl_get_entry(a, ACL_FIRST_ENTRY, &e);
					acl_free(a);
				}
				ok(cnt == 0, "misc: default ACL is empty after delete");
			}
			/* deleting again is still success */
			ok(xacl_del_default_fd(fd) == 0, "misc: xacl_del_default_fd idempotent");
			close(fd);
		}
	}

	/* a freshly-created file has no explicit access ACL xattr -> count 0 */
	if (acl_mkfile("plainfile") == 0) {
		fd = open_held("plainfile");
		ok(fd >= 0, "misc: open plainfile fd");
		if (fd >= 0) {
			int rc = xacl_get_fd(fd, 0, &ents, &n);
			ok(rc == 0 && n == 0, "misc: xacl_get_fd on mode-only file -> no entries");
			if (ents)
				free(ents);
			ents = NULL;
			/* no default ACL on a regular file -> empty */
			rc = xacl_get_fd(fd, 1, &ents, &n);
			ok(rc == 0 && n == 0, "misc: xacl_get_fd default on regular file -> no entries");
			if (ents)
				free(ents);
			close(fd);
		}
	}

	/* at-variant: leaf NOFOLLOW must not touch a symlink target */
	if (xacl_at_available()) {
		int dirfd;
		acl_mkfile("nofollow_target");
		p = acl_path("nofollow_link");
		unlink(p);
		if (symlink("nofollow_target", p) == 0
		 && (dirfd = open(scratch, O_RDONLY | O_DIRECTORY | O_CLOEXEC)) >= 0) {
			rsync_acl_ent before[MAX_ENT], after[MAX_ENT];
			int bc, ac;
			/* give the target a distinctive ACL via libacl */
			libacl_set(acl_path("nofollow_target"), ACL_TYPE_ACCESS, A1, NELEM(A1));
			bc = libacl_get(acl_path("nofollow_target"), ACL_TYPE_ACCESS, before, MAX_ENT);
			/* attempt to set through the symlink leaf with NOFOLLOW: must fail */
			errno = 0;
			ok(xacl_set_at(dirfd, "nofollow_link", 0, A2, NELEM(A2)) != 0,
			   "misc: xacl_set_at on symlink leaf is refused");
			ac = libacl_get(acl_path("nofollow_target"), ACL_TYPE_ACCESS, after, MAX_ENT);
			ok(bc == ac && entries_equal(before, bc, after, ac),
			   "misc: symlink target ACL unchanged by NOFOLLOW set");
			close(dirfd);
		}
	}
}

int main(int argc, char *argv[])
{
	char *p;

	if (argc > 1)
		scratch = argv[1];
	else
		scratch = "/tmp";

	/* Probe filesystem ACL support: set a trivial ACL via libacl. */
	if (acl_mkfile(".acl_probe") != 0) {
		fprintf(stderr, "t_acl: cannot create scratch file in %s\n", scratch);
		return 77;
	}
	p = acl_path(".acl_probe");
	if (libacl_set(p, ACL_TYPE_ACCESS, A1, NELEM(A1)) != 0) {
		if (errno == EOPNOTSUPP || errno == ENOTSUP || errno == ENOSYS) {
			fprintf(stderr, "t_acl: %s has no ACL support -- skipping\n", scratch);
			unlink(p);
			return 77;
		}
		fprintf(stderr, "t_acl: probe acl_set_file failed: %s\n", strerror(errno));
		unlink(p);
		return 77;
	}
	unlink(p);

	fprintf(stderr, "t_acl: scratch=%s, setxattrat=%s\n",
		scratch, xacl_at_available() ? "yes" : "no");

	run_access("a1", A1, NELEM(A1), "named-user+mask");
	run_access("a2", A2, NELEM(A2), "multi-named+mask");
	run_access("a3", A3, NELEM(A3), "large-uid");
	run_access("a4", A4, NELEM(A4), "zero-perms");

	run_default("d1", D1, NELEM(D1), "minimal");
	run_default("d2", D2, NELEM(D2), "named+mask");

	run_misc();

	fprintf(stderr, "t_acl: %d failure(s)\n", errs);
	return errs ? 1 : 0;
}

#endif /* SUPPORT_ACL_FD */

/*
 * Test harness for secure_relative_open()'s front-door input
 * validation. Codex audit Finding 5 noted that the existing check
 *
 *     if (strncmp(relpath, "../", 3) == 0 || strstr(relpath, "/../"))
 *
 * catches "../foo" and "foo/../bar" but misses bare ".." (an actual
 * one-level escape on platforms that fall back to the per-component
 * walk), as well as "a/..", "foo/..", and any other form that
 * decomposes to a ".." component when split on "/". The kernel-
 * enforced RESOLVE_BENEATH (Linux 5.6+) and O_RESOLVE_BENEATH
 * (FreeBSD 13+, macOS 15+) reject these in-kernel; the per-
 * component fallback used on NetBSD, OpenBSD, Solaris, Cygwin and
 * pre-5.6 Linux does not, so the validation must happen at the
 * front door.
 *
 * This helper invokes secure_relative_open() with each suspect
 * input and checks both the failure (rc < 0) and the errno
 * (EINVAL means "rejected at the front door"). Pre-fix, the kernel
 * may reject with a different errno (EXDEV from RESOLVE_BENEATH);
 * post-fix, the front-door check catches every variant up front
 * with a consistent EINVAL across platforms.
 *
 * Not linked into rsync itself.
 */

#include "rsync.h"

#include <sys/stat.h>

int dry_run = 0;
int am_root = 0;
int am_sender = 0;
int read_only = 0;
int list_only = 0;
int copy_links = 0;
int copy_unsafe_links = 0;
extern int am_daemon, am_chrooted;

short info_levels[COUNT_INFO], debug_levels[COUNT_DEBUG];

static int errs = 0;

static void check_relpath(const char *relpath)
{
	int fd;
	int saved_errno;

	errno = 0;
	fd = secure_relative_open(NULL, relpath, O_RDONLY | O_DIRECTORY, 0);
	saved_errno = errno;

	if (fd >= 0) {
		fprintf(stderr,
			"FAIL [relpath=%-12s]: returned valid fd %d (escape) -- expected -1 EINVAL\n",
			relpath, fd);
		close(fd);
		errs++;
		return;
	}

	if (saved_errno != EINVAL) {
		fprintf(stderr,
			"FAIL [relpath=%-12s]: rejected but errno=%d (%s), expected EINVAL\n",
			relpath, saved_errno, strerror(saved_errno));
		errs++;
		return;
	}

	fprintf(stderr, "OK   [relpath=%-12s]: rejected with EINVAL\n", relpath);
}

static void check_basedir(const char *basedir)
{
	int fd;
	int saved_errno;

	errno = 0;
	fd = secure_relative_open(basedir, "ok", O_RDONLY | O_DIRECTORY, 0);
	saved_errno = errno;

	if (fd >= 0) {
		fprintf(stderr,
			"FAIL [basedir=%-12s]: returned valid fd %d -- expected -1 EINVAL\n",
			basedir, fd);
		close(fd);
		errs++;
		return;
	}

	if (saved_errno != EINVAL) {
		fprintf(stderr,
			"FAIL [basedir=%-12s]: rejected but errno=%d (%s), expected EINVAL\n",
			basedir, saved_errno, strerror(saved_errno));
		errs++;
		return;
	}

	fprintf(stderr, "OK   [basedir=%-12s]: rejected with EINVAL\n", basedir);
}

static void check_beneath_dotdot(void)
{
	STRUCT_STAT ast, fst;
	int anchor, fd;

	anchor = open(".", O_RDONLY | O_DIRECTORY);
	if (anchor < 0 || fstat(anchor, &ast) < 0) {
		perror("open/fstat anchor");
		errs++;
		return;
	}

	fd = secure_relative_open_at_beneath(anchor, "alias/../subdir",
					     O_RDONLY | O_DIRECTORY, 0);
	if (fd < 0 || fstat(fd, &fst) < 0 || fst.st_dev != ast.st_dev
	 || fst.st_ino == ast.st_ino) {
		fprintf(stderr, "FAIL [beneath safe]: in-anchor '..' was not resolved\n");
		errs++;
	} else
		fprintf(stderr, "OK   [beneath safe]: in-anchor '..' resolved\n");
	if (fd >= 0)
		close(fd);

	/* A bare final ".." must be refused whatever flags the caller passes.  The
	 * leaf fast paths in secure_walk_at() openat() the final component
	 * directly, so before they learned to defer a literal ".." to ds_descend()
	 * an O_NOFOLLOW caller got back the anchor's own parent, and a caller
	 * without O_DIRECTORY opened it transiently.  Only the O_DIRECTORY form
	 * was ever refused. */
	{
		static const struct { const char *label; int flags; } dotdot_cases[] = {
			{ "O_DIRECTORY",            O_RDONLY | O_DIRECTORY },
			{ "O_DIRECTORY|O_NOFOLLOW", O_RDONLY | O_DIRECTORY | O_NOFOLLOW },
			{ "no O_DIRECTORY",         O_RDONLY },
		};
		unsigned ci;
		for (ci = 0; ci < sizeof dotdot_cases / sizeof *dotdot_cases; ci++) {
			int dfd;
			errno = 0;
			dfd = secure_relative_open_at_beneath(anchor, "..",
							      dotdot_cases[ci].flags, 0);
			if (dfd >= 0) {
				STRUCT_STAT dst;
				int above = fstat(dfd, &dst) == 0 && dst.st_ino != ast.st_ino;
				fprintf(stderr, "FAIL [beneath bare-dotdot %s]: rc=%d%s\n",
					dotdot_cases[ci].label, dfd,
					above ? " -- resolved ABOVE the anchor" : "");
				errs++;
				close(dfd);
			} else if (errno != ELOOP) {
				fprintf(stderr, "FAIL [beneath bare-dotdot %s]: errno=%d, expected ELOOP\n",
					dotdot_cases[ci].label, errno);
				errs++;
			} else
				fprintf(stderr, "OK   [beneath bare-dotdot %s]: refused with ELOOP\n",
					dotdot_cases[ci].label);
		}
	}

	errno = 0;
	fd = secure_relative_open_at_beneath(anchor, "../outside",
					     O_RDONLY | O_DIRECTORY, 0);
	if (fd >= 0 || errno != ELOOP) {
		fprintf(stderr, "FAIL [beneath escape]: rc=%d errno=%d, expected -1/ELOOP\n",
			fd, errno);
		if (fd >= 0)
			close(fd);
		errs++;
	} else
		fprintf(stderr, "OK   [beneath escape]: climb above anchor refused\n");

	close(anchor);
}

int main(int argc, char **argv)
{
	if (argc != 2) {
		fprintf(stderr, "usage: %s <test-dir>\n", argv[0]);
		return 2;
	}
	if (chdir(argv[1]) < 0) {
		perror("chdir");
		return 2;
	}

	/* secure_relative_open's daemon-only confinement protections only
	 * fire when am_daemon && !am_chrooted (the threat model is the
	 * daemon-no-chroot deployment), but the front-door input
	 * validation runs unconditionally. We set am_daemon anyway so the
	 * helper exercises the same code shape the receiver does. */
	am_daemon = 1;
	am_chrooted = 0;

	mkdir("subdir", 0755);
	symlink("subdir", "alias");

	/* Each of these relpaths must be rejected with EINVAL at the
	 * secure_relative_open() front door. ".." is the actual one-level
	 * escape; the others ("subdir/..", "subdir/../subdir") resolve
	 * back to the start dir on systems that allow them, but we still
	 * reject them as defence-in-depth: a path containing a ".." token
	 * is suspicious and the caller should normalise before passing
	 * it in. The "../foo" / "foo/../bar" / "/foo" / "/" cases are
	 * regression checks for the existing checks. */
	check_relpath("..");
	check_relpath("../foo");
	check_relpath("subdir/..");
	check_relpath("subdir/../subdir");
	check_relpath("foo/../bar");
	check_relpath("/foo");
	check_relpath("/");

	/* Same checks against basedir (which the codex Finding 2 fix
	 * routes through the same RESOLVE_BENEATH-equivalent). Absolute
	 * basedirs are operator-trusted and intentionally not validated
	 * here. */
	check_basedir("..");
	check_basedir("../subdir");
	check_basedir("subdir/..");
	check_basedir("foo/../bar");

	/* A followed symlink target is the one caller that legitimately carries
	 * literal '..'.  Its dedicated fd-anchored entry point must preserve an
	 * in-tree climb while refusing to pop above the anchor. */
	check_beneath_dotdot();

	if (errs)
		fprintf(stderr, "\n%d failure(s)\n", errs);
	return errs ? 1 : 0;
}

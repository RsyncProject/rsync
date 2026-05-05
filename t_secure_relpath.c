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

	if (errs)
		fprintf(stderr, "\n%d failure(s)\n", errs);
	return errs ? 1 : 0;
}

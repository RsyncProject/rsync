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

#ifdef __linux__
#include <sys/syscall.h>
#include <linux/openat2.h>
#endif

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

/* Probe the running kernel for the RESOLVE_BENEATH-equivalent confinement
 * that secure_relative_open() prefers over the per-component O_NOFOLLOW
 * walk.  Returns 1 if either openat2(RESOLVE_BENEATH) on Linux 5.6+ or
 * openat(O_RESOLVE_BENEATH) on FreeBSD 13+ / macOS 15+ is honoured by
 * the running kernel, 0 otherwise.  The probe opens "." (a directory
 * the helper has just chdir'd into) so it can't fail for any reason
 * other than the kernel rejecting the requested confinement flag. */
static int kernel_resolve_beneath_supported(void)
{
	int fd;
#if defined __linux__
	struct open_how how;
	memset(&how, 0, sizeof how);
	how.flags = O_RDONLY | O_DIRECTORY;
	how.resolve = RESOLVE_BENEATH | RESOLVE_NO_MAGICLINKS;
	fd = syscall(SYS_openat2, AT_FDCWD, ".", &how, sizeof how);
	if (fd >= 0) {
		close(fd);
		return 1;
	}
	/* O_RESOLVE_BENEATH is not defined on Linux, and even if it were, Linux's
	 * openat(2) does not return -EINVAL for unknown flag bits and so if
	 * O_RESOLVE_BENEATH happened to get defined somehow, the following
	 * fallback would always return success. */
#elif defined O_RESOLVE_BENEATH
	fd = openat(AT_FDCWD, ".", O_RDONLY | O_DIRECTORY | O_RESOLVE_BENEATH);
	if (fd >= 0) {
		close(fd);
		return 1;
	}
#endif
	return 0;
}

static void check_relative_open(const char *basedir, const char *relpath,
								int want_errno)
{
	int fd;
	int saved_errno;

	errno = 0;
	fd = secure_relative_open(basedir, relpath, O_RDONLY | O_DIRECTORY, 0);
	saved_errno = errno;

	if (fd >= 0) {
		close(fd);
		if (want_errno != 0) {
			fprintf(stderr,
				"FAIL [basedir=%-12s relpath=%-12s]: returned valid fd %d (escape) -- expected -1 errno=%d (%s)\n",
				basedir, relpath, fd, want_errno, strerror(want_errno));
			errs++;
			return;
		}
	} else if (saved_errno != want_errno) {
		fprintf(stderr,
			"FAIL [basedir=%-12s relpath=%-12s]: rejected but errno=%d (%s), expected errno=%d (%s)\n",
			basedir, relpath, saved_errno, strerror(saved_errno), want_errno, strerror(want_errno));
		errs++;
		return;
	}

	fprintf(stderr,
		"OK   [basedir=%-12s relpath=%-12s]: rejected with errno %d (%s)\n",
		basedir, relpath, saved_errno, strerror(saved_errno));
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

	/* On systems with no RESOLVE_BENEATH, ".." at any point is an error. */
	int contained_dotdot_errno = 0;
	if (!kernel_resolve_beneath_supported())
		contained_dotdot_errno = EXDEV;

	/* secure_relative_open's daemon-only confinement protections only
	 * fire when am_daemon && !am_chrooted (the threat model is the
	 * daemon-no-chroot deployment), but the front-door input
	 * validation runs unconditionally. We set am_daemon anyway so the
	 * helper exercises the same code shape the receiver does. */
	am_daemon = 1;
	am_chrooted = 0;

	mkdir("ok", 0755);
	mkdir("subdir", 0755);
	mkdir("subdir/ok", 0755);
	mkdir("foo", 0755);
	mkdir("foo/ok", 0755);
	mkdir("bar", 0755);
	mkdir("bar/ok", 0755);

	/* ".." that jumps outside of the root is rejected with EXDEV. */
	check_relative_open(NULL, "..", EXDEV);
	check_relative_open(NULL, "../foo", EXDEV);
	/* Absolute paths for relpath are rejected with EINVAL. */
	check_relative_open(NULL, "/foo", EINVAL);
	check_relative_open(NULL, "/", EINVAL);
	/* If RESOLVE_BENEATH is supported, ".." components that are contained
	 * within the root are permitted. On older systems, they are rejected with
	 * EXDEV. */
	check_relative_open(NULL, "subdir/..", contained_dotdot_errno);
	check_relative_open(NULL, "subdir/../subdir", contained_dotdot_errno);
	check_relative_open(NULL, "foo/../bar", contained_dotdot_errno);

	/* Same checks against basedir (which the codex Finding 2 fix
	 * routes through the same RESOLVE_BENEATH-equivalent). Absolute
	 * basedirs are operator-trusted and intentionally not validated
	 * here. */
	check_relative_open("..", "ok", EXDEV);
	check_relative_open("../subdir", "ok", EXDEV);
	check_relative_open("subdir/..", "ok", contained_dotdot_errno);
	check_relative_open("subdir/../subdir", "ok", contained_dotdot_errno);
	check_relative_open("foo/../bar", "ok", contained_dotdot_errno);

	if (errs)
		fprintf(stderr, "\n%d failure(s)\n", errs);
	return errs ? 1 : 0;
}

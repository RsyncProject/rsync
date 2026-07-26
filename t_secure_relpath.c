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
 * Run with a second argument of "semantics" it instead checks the
 * resolution semantics that both resolver tiers (the kernel
 * RESOLVE_BENEATH fast paths and the per-component O_NOFOLLOW walk
 * fallback) must agree on: creating a missing final component with
 * O_CREAT, ENOENT for a missing intermediate component, opening an
 * existing file, refusing an out-of-tree symlink in the final
 * component, and O_DIRECTORY requests. The create case is a
 * regression test for the fallback returning ENOENT instead of
 * creating the file (which broke the non-chroot daemon receiver
 * with --inplace); the fallback tier is exercised natively on
 * NetBSD/OpenBSD/Solaris/Cygwin and by --disable-openat2 builds.
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

static void check_open_ok(const char *relpath, int flags, mode_t mode, int want_dir, const char *what)
{
	struct stat sb;
	int fd = secure_relative_open(NULL, relpath, flags, mode);

	if (fd < 0) {
		fprintf(stderr, "FAIL [%-20s]: %s: expected success, got errno=%d (%s)\n",
			relpath, what, errno, strerror(errno));
		errs++;
		return;
	}
	if (fstat(fd, &sb) == 0 && want_dir != S_ISDIR(sb.st_mode)) {
		fprintf(stderr, "FAIL [%-20s]: %s: opened a %s, expected a %s\n",
			relpath, what, S_ISDIR(sb.st_mode) ? "directory" : "file",
			want_dir ? "directory" : "file");
		close(fd);
		errs++;
		return;
	}
	close(fd);
	fprintf(stderr, "OK   [%-20s]: %s\n", relpath, what);
}

static void check_open_fail(const char *relpath, int flags, int want_errno, const char *what)
{
	/* openat2() rejects a nonzero mode without O_CREAT (EINVAL), so
	 * only pass one when creating -- as real callers do. */
	mode_t mode = flags & O_CREAT ? 0600 : 0;
	int fd = secure_relative_open(NULL, relpath, flags, mode);
	int saved_errno = errno;

	if (fd >= 0) {
		fprintf(stderr, "FAIL [%-20s]: %s: expected failure but got fd %d\n",
			relpath, what, fd);
		close(fd);
		errs++;
		return;
	}
	/* want_errno 0 = any errno is acceptable (the exact code differs
	 * between the resolver tiers); it just must fail. */
	if (want_errno && saved_errno != want_errno) {
		fprintf(stderr, "FAIL [%-20s]: %s: errno=%d (%s), expected %d (%s)\n",
			relpath, what, saved_errno, strerror(saved_errno),
			want_errno, strerror(want_errno));
		errs++;
		return;
	}
	fprintf(stderr, "OK   [%-20s]: %s (errno=%s)\n", relpath, what, strerror(saved_errno));
}

static void check_presence(const char *path, int want_present, const char *what)
{
	struct stat sb;
	int present = lstat(path, &sb) == 0;

	if (present != want_present) {
		fprintf(stderr, "FAIL [%-20s]: %s: %s\n",
			path, what, present ? "unexpectedly exists" : "does not exist");
		errs++;
		return;
	}
	fprintf(stderr, "OK   [%-20s]: %s\n", path, what);
}

static int run_semantics_checks(void)
{
	char cwd[MAXPATHLEN], esc_target[MAXPATHLEN];
	int fd, have_symlink;

	if (mkdir("subdir", 0755) < 0 && errno != EEXIST) {
		perror("mkdir subdir");
		return 2;
	}
	fd = open("subdir/existing.txt", O_WRONLY | O_CREAT, 0644);
	if (fd < 0) {
		perror("create subdir/existing.txt");
		return 2;
	}
	close(fd);

	/* An ABSOLUTE symlink target outside the anchor directory: refused
	 * by both tiers (the fast paths reject the escape in-kernel, the
	 * fallback refuses any symlink via O_NOFOLLOW). A within-tree
	 * symlink would be tier-dependent (the kernel paths follow it, the
	 * fallback does not), so it is deliberately not tested here. */
	if (!getcwd(cwd, sizeof cwd)) {
		perror("getcwd");
		return 2;
	}
	if ((size_t)snprintf(esc_target, sizeof esc_target,
			     "%s/../t_secure_relpath_escape.tmp", cwd) >= sizeof esc_target) {
		fprintf(stderr, "escape target path too long\n");
		return 2;
	}
	unlink(esc_target);
	have_symlink = symlink(esc_target, "subdir/esclink") == 0;

	/* A missing final component with O_CREAT must be created (this is
	 * the non-chroot daemon receiver shape for --inplace: relpath with
	 * no basedir). Regression: the walk fallback returned ENOENT. */
	check_open_ok("newfile.txt", O_WRONLY | O_CREAT, 0600, 0,
		      "create missing final component");
	check_presence("newfile.txt", 1, "created file is present");
	check_open_ok("subdir/newfile2.txt", O_WRONLY | O_CREAT, 0600, 0,
		      "create missing final component (nested)");
	check_presence("subdir/newfile2.txt", 1, "created nested file is present");

	/* A missing INTERMEDIATE component is a genuine ENOENT and must
	 * not create anything. */
	check_open_fail("missingdir/new.txt", O_WRONLY | O_CREAT, ENOENT,
			"missing intermediate component");
	check_presence("missingdir", 0, "missing intermediate not created");

	/* Existing regular file: readable, and O_CREAT without O_EXCL
	 * opens it (the receiver reopens existing files this way). */
	check_open_ok("subdir/existing.txt", O_RDONLY, 0, 0,
		      "open existing file O_RDONLY");
	check_open_ok("subdir/existing.txt", O_WRONLY | O_CREAT, 0600, 0,
		      "reopen existing file O_WRONLY|O_CREAT");

	/* A symlink in the final component pointing outside the tree must
	 * be refused, and O_CREAT must not create the escape target. The
	 * errno differs by tier (ELOOP from the walk, EXDEV & co from
	 * RESOLVE_BENEATH), so only the refusal itself is checked. */
	if (have_symlink) {
		check_open_fail("subdir/esclink", O_WRONLY | O_CREAT, 0,
				"refuse out-of-tree symlink final component");
		check_presence(esc_target, 0, "escape target not created");
	} else
		fprintf(stderr, "SKIP [subdir/esclink      ]: symlink() unsupported here\n");

	/* O_DIRECTORY: an existing directory opens (and is a directory);
	 * a missing name stays ENOENT -- O_CREAT-style creation must not
	 * kick in for directory requests. */
	check_open_ok("subdir", O_RDONLY | O_DIRECTORY, 0, 1,
		      "open existing dir O_DIRECTORY");
	check_open_fail("missingdir2", O_RDONLY | O_DIRECTORY, ENOENT,
			"missing dir with O_DIRECTORY");
	check_presence("missingdir2", 0, "missing dir not created");

	if (errs)
		fprintf(stderr, "\n%d failure(s)\n", errs);
	return errs ? 1 : 0;
}

int main(int argc, char **argv)
{
	if (argc != 2 && !(argc == 3 && strcmp(argv[2], "semantics") == 0)) {
		fprintf(stderr, "usage: %s <test-dir> [semantics]\n", argv[0]);
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

	if (argc == 3)
		return run_semantics_checks();

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

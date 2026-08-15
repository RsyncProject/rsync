/*
 * Test harness for vfs_rename_at(): a mixed top-level/slashed rename must still
 * resolve the slashed side's parent under vfs_resolve_open() rather than
 * fall back to plain rename(). Not linked into rsync. GPL version 2.
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

#ifdef AT_FDCWD
/* The 3.4.3 bug: if either side has no slash the whole op fell back to plain
 * rename(), leaving the slashed side's parent outside vfs_resolve_open(). */
static int vulnerable_mixed_rename_at(const char *old_path, const char *new_path)
{
	const char *old_slash, *new_slash;

	if (!old_path || !*old_path || *old_path == '/'
	 || !new_path || !*new_path || *new_path == '/')
		return vfs_rename(old_path, new_path);

	old_slash = strrchr(old_path, '/');
	new_slash = strrchr(new_path, '/');
	if (!old_slash || !new_slash)
		return vfs_rename(old_path, new_path);

	return vfs_rename_at(old_path, new_path, 0, 0);
}
#endif

static void check_exists(const char *label, const char *path, int expect_exists)
{
	int exists = access(path, F_OK) == 0;

	if (exists != expect_exists) {
		fprintf(stderr, "FAIL [%s]: %s %s, expected %s\n",
			label, path, exists ? "exists" : "does not exist",
			expect_exists ? "exists" : "does not exist");
		errs++;
		return;
	}
	fprintf(stderr, "OK   [%s]: %s %s\n",
		label, path, exists ? "exists" : "does not exist");
}

static void check_rename(const char *label, const char *old_path,
			 const char *new_path, int expect_ok)
{
	int rc;
	int got_ok;
	int saved_errno;

	errno = 0;
	rc = vfs_rename_at(old_path, new_path, 0, 0);
	saved_errno = errno;
	got_ok = rc == 0;

	if (got_ok != expect_ok) {
		fprintf(stderr, "FAIL [%s]: rename %s -> %s rc=%d errno=%d (%s), expected %s\n",
			label, old_path, new_path, rc, saved_errno,
			strerror(saved_errno), expect_ok ? "success" : "rejection");
		errs++;
		return;
	}
	fprintf(stderr, "OK   [%s]: rename %s -> %s %s\n",
		label, old_path, new_path, expect_ok ? "succeeded" : "rejected");
}

/* Like check_rename() but with explicit per-operand policy flags, for the
 * two-path per-side split (PR #30). */
static void check_rename_flags(const char *label, const char *old_path,
			       const char *new_path, int old_flags, int new_flags,
			       int expect_ok)
{
	int rc, got_ok, saved_errno;

	errno = 0;
	rc = vfs_rename_at(old_path, new_path, old_flags, new_flags);
	saved_errno = errno;
	got_ok = rc == 0;

	if (got_ok != expect_ok) {
		fprintf(stderr, "FAIL [%s]: rename %s -> %s (of=%d nf=%d) rc=%d errno=%d (%s), expected %s\n",
			label, old_path, new_path, old_flags, new_flags, rc, saved_errno,
			strerror(saved_errno), expect_ok ? "success" : "rejection");
		errs++;
		return;
	}
	fprintf(stderr, "OK   [%s]: rename %s -> %s %s\n",
		label, old_path, new_path, expect_ok ? "succeeded" : "rejected");
}

static void check_vulnerable_rename(const char *label, const char *old_path,
				    const char *new_path)
{
#ifdef AT_FDCWD
	int rc;
	int saved_errno;

	errno = 0;
	rc = vulnerable_mixed_rename_at(old_path, new_path);
	saved_errno = errno;

	if (rc != 0) {
		fprintf(stderr, "FAIL [%s]: vulnerable rename %s -> %s rc=%d errno=%d (%s), expected escape\n",
			label, old_path, new_path, rc, saved_errno,
			strerror(saved_errno));
		errs++;
		return;
	}
	fprintf(stderr, "OK   [%s]: vulnerable rename %s -> %s escaped\n",
		label, old_path, new_path);
#else
	fprintf(stderr, "SKIP [%s]: AT_FDCWD not available\n", label);
#endif
}

static int run_escape_poc(const char *module_dir)
{
#ifndef AT_FDCWD
	fprintf(stderr, "SKIP: AT_FDCWD not available\n");
	return 77;
#else
	if (chdir(module_dir) < 0) {
		perror("chdir");
		return 2;
	}

	am_daemon = 1;
	am_chrooted = 0;

	check_vulnerable_rename("P1: 3.4.3-style top-level source to escaping destination parent",
				"poc-top-to-escape", "escape_link/vuln-created");
	check_exists("P1 source consumed", "poc-top-to-escape", 0);
	check_exists("P1 outside destination created", "../trap/vuln-created", 1);

	check_vulnerable_rename("P2: 3.4.3-style escaping source parent to top-level destination",
				"escape_link/poc-outside-source", "vuln-stolen");
	check_exists("P2 outside source consumed", "../trap/poc-outside-source", 0);
	check_exists("P2 destination created in module", "vuln-stolen", 1);

	check_rename("P3: fixed top-level source to escaping destination parent",
		     "fixed-top-to-escape", "escape_link/fixed-created", 0);
	check_exists("P3 source preserved", "fixed-top-to-escape", 1);
	check_exists("P3 outside destination absent", "../trap/fixed-created", 0);

	check_rename("P4: fixed escaping source parent to top-level destination",
		     "escape_link/fixed-outside-source", "fixed-stolen", 0);
	check_exists("P4 outside source preserved", "../trap/fixed-outside-source", 1);
	check_exists("P4 destination absent", "fixed-stolen", 0);

	if (errs)
		fprintf(stderr, "%d failure(s)\n", errs);
	return errs ? 1 : 0;
#endif
}

int main(int argc, char **argv)
{
	if (argc == 3 && strcmp(argv[1], "--poc") == 0)
		return run_escape_poc(argv[2]);

	if (argc != 2) {
		fprintf(stderr, "usage: %s [--poc] <module-dir>\n", argv[0]);
		return 2;
	}

#ifndef AT_FDCWD
	fprintf(stderr, "SKIP: AT_FDCWD not available\n");
	return 77;
#else
	if (chdir(argv[1]) < 0) {
		perror("chdir");
		return 2;
	}

	am_daemon = 1;
	am_chrooted = 0;

	/* Plain mixed paths must keep working. */
	check_rename("A: top-level source to slashed destination",
		     "top-to-dir", "realdir/top-to-dir", 1);
	check_exists("A source consumed", "top-to-dir", 0);
	check_exists("A destination created", "realdir/top-to-dir", 1);

	check_rename("B: slashed source to top-level destination",
		     "realdir/dir-to-top", "dir-to-top", 1);
	check_exists("B source consumed", "realdir/dir-to-top", 0);
	check_exists("B destination created", "dir-to-top", 1);

	/* A slashed destination parent that escapes the module must be rejected. */
	check_rename("C: top-level source to escaping destination parent",
		     "top-to-escape", "escape_link/new-outside", 0);
	check_exists("C source preserved", "top-to-escape", 1);
	check_exists("C outside destination absent", "../trap/new-outside", 0);

	/* A slashed source parent that escapes the module must be rejected too. */
	check_rename("D: escaping source parent to top-level destination",
		     "escape_link/outside-source", "stolen-from-outside", 0);
	check_exists("D outside source preserved", "../trap/outside-source", 1);
	check_exists("D destination absent", "stolen-from-outside", 0);

	check_rename("E: shared slashed parent",
		     "realdir/same-old", "realdir/same-new", 1);
	check_exists("E source consumed", "realdir/same-old", 0);
	check_exists("E destination created", "realdir/same-new", 1);

	check_rename("F: top-level source to top-level destination",
		     "top-old", "top-new", 1);
	check_exists("F source consumed", "top-old", 0);
	check_exists("F destination created", "top-new", 1);

	/* Per-operand policy split (PR #30): the NEW side's policy must be independent
	 * of the OLD side's.  oplink is a caller-owned (uid0/euid) symlink that ESCAPES
	 * the tree (-> ../trap): the ownership walk (operator policy) follows the
	 * operator's own symlink, but the secure receiver resolve (transfer, flag 0)
	 * refuses it because it leaves the cwd anchor.  PS-refuse and PS-follow rename
	 * to the SAME oplink/ path with the SAME operator old side, differing ONLY in
	 * the new-side flag -- so the per-side flag, not a whole-call flag, decides.
	 * The old single-flag API applied VFS_OPERATOR_PATH to both operands, so it
	 * would have followed oplink in PS-refuse too (PS-refuse is RED on that code).
	 * Run non-daemon (the regime where an operator basis/backup path legitimately
	 * carries the operator's own uid0/euid symlinks). */
	{
		struct stat lst;
		if (lstat("oplink", &lst) == 0 && S_ISLNK(lst.st_mode)) {
			int save_daemon = am_daemon;
			am_daemon = 0;

			check_rename_flags("PS-refuse: new side transfer refuses an escaping owned symlink",
				"realdir/perside-src2", "oplink/tr-out",
				VFS_OPERATOR_PATH, 0, 0);
			check_exists("PS-refuse out-of-tree dest absent", "../trap/tr-out", 0);
			check_exists("PS-refuse source preserved", "realdir/perside-src2", 1);

			check_rename_flags("PS-follow: new side operator follows the same owned symlink",
				"realdir/perside-src3", "oplink/op-out",
				VFS_OPERATOR_PATH, VFS_OPERATOR_PATH, 1);
			check_exists("PS-follow out-of-tree dest created (operator's own symlink)", "../trap/op-out", 1);

			am_daemon = save_daemon;
		}
	}

	if (errs)
		fprintf(stderr, "%d failure(s)\n", errs);
	return errs ? 1 : 0;
#endif
}

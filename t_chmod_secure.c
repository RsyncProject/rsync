/*
 * Test harness for do_chmod_at(). Confirms the symlink-TOCTOU
 * primitive used by CVE-2026-29518 (and its incomplete-fix follow-up
 * for chmod) is closed by do_chmod_at(): a parent directory component
 * being a symlink that escapes the receiver's confinement must be
 * rejected, while a parent symlink that resolves *within* the tree
 * must still work (so legitimate dir-symlinks are not regressed).
 *
 * Not linked into rsync itself.
 *
 * This program is free software; you can redistribute it and/or modify
 * it under the terms of the GNU General Public License version 2 as
 * published by the Free Software Foundation.
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

static void check(const char *label, int actual_rc, int expect_ok,
		  const char *path, mode_t expected_mode)
{
	struct stat st;
	int got_ok = (actual_rc == 0);
	if (got_ok != expect_ok) {
		fprintf(stderr, "FAIL [%s]: rc=%d errno=%d (%s), expected %s\n",
			label, actual_rc, errno, strerror(errno),
			expect_ok ? "success" : "rejection");
		errs++;
		return;
	}
	if (path && stat(path, &st) < 0) {
		fprintf(stderr, "FAIL [%s]: stat(%s) failed: %s\n",
			label, path, strerror(errno));
		errs++;
		return;
	}
	if (path && (st.st_mode & 07777) != expected_mode) {
		fprintf(stderr,
			"FAIL [%s]: %s mode is 0%o, expected 0%o\n",
			label, path, st.st_mode & 07777, expected_mode);
		errs++;
		return;
	}
	fprintf(stderr, "OK   [%s]\n", label);
}

int main(int argc, char **argv)
{
	if (argc != 2) {
		fprintf(stderr, "usage: %s <module-dir>\n", argv[0]);
		return 2;
	}
	if (chdir(argv[1]) < 0) {
		perror("chdir");
		return 2;
	}

	/* Simulate the daemon-without-chroot deployment that do_chmod_at()
	 * defends. With am_daemon=0 or am_chrooted=1 the wrapper falls
	 * through to plain do_chmod() and the symlink-race test would be
	 * meaningless. */
	am_daemon = 1;
	am_chrooted = 0;

	/* Test layout (all inside the directory we just chdir'd to):
	 *
	 *     ./realdir/sentinel        -- regular target file
	 *     ./inside_link -> realdir  -- legitimate dir-symlink within the tree
	 *     ./escape_link -> ../trap  -- attacker swap, target outside tree
	 *     ../trap/sentinel          -- the file the attacker wants to alter
	 *
	 * The shell wrapper that calls this helper has set both sentinel
	 * files to mode 0600 so we have a clean baseline to compare.
	 */

	/* Scenario A: legitimate parent dir-symlink, chmod must succeed. */
	int rc = do_chmod_at("inside_link/sentinel", 0640);
	check("A: legit dir-symlink within tree",
	      rc, 1, "realdir/sentinel", 0640);

	/* Scenario B: parent symlink escapes the tree -- chmod must be
	 * rejected and the outside file's mode must be unchanged. */
	rc = do_chmod_at("escape_link/sentinel", 0666);
	check("B: parent symlink escapes tree (the attack)",
	      rc, 0, "../trap/sentinel", 0600);

	/* Scenario C: plain relative path with no symlink components,
	 * regression check that the safe wrapper doesn't break the
	 * normal case. */
	rc = do_chmod_at("realdir/sentinel", 0644);
	check("C: plain relative path (regression check)",
	      rc, 1, "realdir/sentinel", 0644);

	/* Scenario D: top-level file, no parent directory component.
	 * Falls back to do_chmod(); should succeed. */
	rc = do_chmod_at("topfile", 0640);
	check("D: top-level file, no parent component",
	      rc, 1, "topfile", 0640);

	if (errs)
		fprintf(stderr, "%d failure(s)\n", errs);
	return errs ? 1 : 0;
}

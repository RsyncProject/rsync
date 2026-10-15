#!/usr/bin/env python3
# Python rewrite of testsuite/missing.test.
#
# Three regressions guarded by the missing_below logic rewrite:
#   1. Dry-run with --ignore-non-existing must NOT emit "not creating new"
#      for files whose containing directory already exists at the dest.
#   2. Dry-run -R -y --no-implied-dirs must not crash trying to build a
#      fuzzy dirlist for a directory it never created.
#   3. --delete-after dry-run still emits "*deleting" lines even when the
#      last source dir is dry-missing on the destination.

import os
import subprocess

from rsyncfns import FROMDIR, RSYNC, TMPDIR, TODIR, makepath, rsync_argv, test_fail


makepath(FROMDIR / 'subdir', TODIR)
(FROMDIR / 'subdir' / 'file').write_text("data\n")
(TODIR / 'other').write_text("data\n")


def run_capture(*args):
    proc = subprocess.run(rsync_argv(*args), capture_output=True, text=True)
    print(proc.stdout, end='')
    print(proc.stderr, end='')
    return proc


# Test 1: too much "not creating new..." output on a dry-run.
out_path = TMPDIR / 'out1'
proc = run_capture('-n', '-r', '--ignore-non-existing', '-vv',
                   f'{FROMDIR}/', f'{TODIR}/')
if proc.returncode != 0:
    test_fail(f"test 1 failed: dry-run errored (rc={proc.returncode})")
out_path.write_text(proc.stdout)
for line in proc.stdout.splitlines():
    if 'not creating new' in line and 'subdir/file' in line:
        test_fail("test 1 failed: dry-run announced creating subdir/file")

# Test 2: fuzzy dirlist crash on dry-run.  Skipped on protocol 29 just like
# the shell version did, since the new flist sanity check rejects this.
if 'protocol=29' not in RSYNC:
    proc = run_capture('-n', '-r', '-R', '--no-implied-dirs', '-y',
                       f'{FROMDIR}/./subdir/file', f'{TODIR}/')
    if proc.returncode != 0:
        test_fail("test 2 failed: --no-implied-dirs dry-run errored")
else:
    print("Skipped test 2 for protocol 29.")

# Test 3: --delete-after pass skipped when last dir is dry-missing.
proc = run_capture('-n', '-r', '--delete-after', '-i',
                   f'{FROMDIR}/', f'{TODIR}/')
saw_delete = any(line.lstrip().startswith('*deleting')
                 and 'other' in line
                 for line in proc.stdout.splitlines())
if not saw_delete:
    test_fail("test 3 failed: no '*deleting other' line in dry-run output")

#!/usr/bin/env python3
# Python rewrite of testsuite/duplicates.test.
#
# The same source directory can be listed many times on the command line
# (e.g. through shell globbing). clean_flist() is supposed to dedupe so
# each file/link is copied exactly once even with ten identical sources.

import os
import subprocess

from rsyncfns import (
    FROMDIR, TODIR,
    rsync_argv, rsync_ls_lR, test_fail,
)


# Build a single regular file plus a symlink to it.
FROMDIR.mkdir(parents=True, exist_ok=True)
name1 = FROMDIR / 'name1'
name2 = FROMDIR / 'name2'
name1.write_text("This is the file\n")
try:
    os.symlink(str(name1), name2)
except OSError as e:
    test_fail(f"can't create symlink: {e}")

# Drive rsync with the same source ten times. Capture the verbose output to
# inspect for duplicate-copy behaviour AND for the dir-listing comparison
# that the shell test's checkit was doing alongside.
sources = [f'{FROMDIR}/'] * 10
proc = subprocess.run(
    rsync_argv('-avv', *sources, f'{TODIR}/'),
    capture_output=True, text=True,
)
print(proc.stdout)
if proc.returncode != 0:
    test_fail(f"rsync exited {proc.returncode}\n{proc.stderr}")

name1_count = sum(1 for ln in proc.stdout.splitlines() if ln == 'name1')
if name1_count != 1:
    test_fail(f"name1 was not copied exactly once (got {name1_count})")

name2_count = sum(1 for ln in proc.stdout.splitlines() if ln.startswith('name2 -> '))
if name2_count != 1:
    test_fail(f"name2 was not copied exactly once (got {name2_count})")

# Cross-check that the destination matches the source.
if rsync_ls_lR(FROMDIR) != rsync_ls_lR(TODIR):
    test_fail("destination listing differs from source after deduplication")

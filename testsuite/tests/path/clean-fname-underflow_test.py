#!/usr/bin/env python3
# Python rewrite of testsuite/clean-fname-underflow.test.
#
# Ensure clean_fname() does not read-before-buffer when collapsing "..".
# Exercises the --server path where a crafted merge filename hits
# clean_fname(); the test fails if rsync dies from a signal (status >= 128)
# AND if rsync exits 0 -- the bogus input must be rejected (clean_fname
# collapses "a/../test" to "test", whose merge file does not exist, so rsync
# errors out non-zero). Accepting it would mean the name was mis-collapsed.

import os
import subprocess

from rsyncfns import TMPDIR, rsync_argv, test_fail


workdir = TMPDIR / 'workdir'
(workdir / 'mod').mkdir(parents=True, exist_ok=True)
os.chdir(workdir)

proc = subprocess.run(
    rsync_argv('--server', '--sender', '-vlr',
               '--filter=merge a/../test', '.', 'mod/'),
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
)

if proc.returncode >= 128:
    test_fail(f"rsync exited due to a signal (status={proc.returncode})")
if proc.returncode == 0:
    test_fail("rsync accepted the bogus 'a/../test' merge filter (expected a "
              "non-zero rejection); clean_fname() may have mis-collapsed it")

print("OK: clean_fname() handled 'a/../test' without crashing, and rejected it")

#!/usr/bin/env python3

import os
import subprocess

from harness.rsync import TMPDIR, rsync_argv, test_fail

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

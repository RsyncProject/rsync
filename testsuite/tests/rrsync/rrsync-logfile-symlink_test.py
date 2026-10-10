#!/usr/bin/env python3
import os
import subprocess

from rsyncfns import SCRATCHDIR, test_fail
from rsyncfns import patched_rrsync

base = SCRATCHDIR / 'rrsync-logfile'
restricted = base / 'restricted'
outside = base / 'outside.log'
base.mkdir(parents=True, exist_ok=True)
restricted.mkdir(exist_ok=True)
outside.write_text('ORIGINAL\n')
os.symlink(outside, base / 'rrsync.log')
rrsync = patched_rrsync(base)

env = {**os.environ, 'SSH_ORIGINAL_COMMAND': 'rsync --server --sender . .'}
proc = subprocess.run([str(rrsync), '-ro', '-no-lock', str(restricted)],
                      cwd=base, env=env, stdout=subprocess.PIPE,
                      stderr=subprocess.PIPE, text=True)
if proc.returncode != 0:
    test_fail(f"patched rrsync command failed unexpectedly:\n{proc.stderr}")
if outside.read_text() != 'ORIGINAL\n':
    test_fail("rrsync followed a symlinked LOGFILE and appended outside the restricted policy")

print("rrsync-logfile-symlink: LOGFILE symlink is not followed")

#!/usr/bin/env python3

import os
import subprocess

from harness.rsync import SCRATCHDIR, rmtree, rsync_argv, test_fail

base = SCRATCHDIR / 'relative-implied'
rmtree(base)
base.mkdir(parents=True)

src = base / 'src'
(src / 'realdir').mkdir(parents=True)
(src / 'realdir' / 'file').write_text('F\n')
os.symlink('realdir', src / 'link')

dst = base / 'dst'
rmtree(dst)
dst.mkdir()
subprocess.run(rsync_argv('-a', '-R', f'{src}/./link/file', f'{dst}/'),
               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

p = dst / 'link'
if p.is_symlink():
    test_fail("--relative: implied 'link' was sent as a symlink, not materialized "
              "as a real directory (the 'always sends ... as real directories' "
              "claim would be false)")
if not p.is_dir():
    test_fail("--relative: implied 'link' was not created as a directory")
f = dst / 'link' / 'file'
if not f.is_file() or f.read_text() != 'F\n':
    test_fail("--relative: deep file did not land under the materialized dir")

print("relative-implied-symlink: -R materializes an implied path element as a "
      "REAL directory on the receiver even when it is a symlink on the sender -- "
      "the 'always sends implied dirs as real directories' claim holds")

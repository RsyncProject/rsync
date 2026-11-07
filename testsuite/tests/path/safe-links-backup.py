#!/usr/bin/env python3

import os
import subprocess

from harness.rsync import SCRATCHDIR, rmtree, rsync_argv, test_fail

base = SCRATCHDIR / 'safe-links-backup'

ESCAPE_TARGET = '../../sensitive'

def build():
    rmtree(base)
    src = base / 'src'
    dest = base / 'dest'
    src.mkdir(parents=True)
    dest.mkdir(parents=True)
    (src / 'keep.txt').write_text('source regular file\n')
    (src / 'escape').write_text('replacement regular file\n')
    os.symlink(ESCAPE_TARGET, dest / 'escape')
    return src, dest

def run(src, dest, *extra):
    proc = subprocess.run(
        rsync_argv('-a', '--backup', '--suffix=.bak', *extra,
                   f'{src}/', f'{dest}/'),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    return proc

src, dest = build()
proc = run(src, dest, '--safe-links')
if proc.returncode != 0:
    test_fail("KI-72: rsync exited %d with --safe-links\n%s%s"
              % (proc.returncode, proc.stdout, proc.stderr))

bak = dest / 'escape.bak'
if os.path.lexists(bak):
    test_fail(
        "KI-72: --safe-links was bypassed on the backup hard-link path: the "
        "escaping symlink was backed up to %s (target %r) instead of being "
        "dropped." % (bak, os.readlink(bak) if os.path.islink(bak) else '?'))

if not (dest / 'escape').is_file():
    test_fail("KI-72: the replacement regular file 'escape' was not transferred")

src, dest = build()
proc = run(src, dest)
if proc.returncode != 0:
    test_fail("KI-72: rsync exited %d without --safe-links\n%s%s"
              % (proc.returncode, proc.stdout, proc.stderr))

bak = dest / 'escape.bak'
if not os.path.islink(bak):
    test_fail("KI-72: without --safe-links the escaping symlink should have "
              "been backed up, but %s is missing or not a symlink." % bak)
if os.readlink(bak) != ESCAPE_TARGET:
    test_fail("KI-72: backup symlink points to %r, expected %r"
              % (os.readlink(bak), ESCAPE_TARGET))

rmtree(base)
src = base / 'src'
dest = base / 'dest'
src.mkdir(parents=True)
dest.mkdir(parents=True)
(src / 'keep.txt').write_text('source regular file\n')
(src / 'link').write_text('replacement regular file\n')
os.symlink('keep.txt', dest / 'link')

proc = run(src, dest, '--safe-links')
if proc.returncode != 0:
    test_fail("KI-72: rsync exited %d backing up a safe symlink\n%s%s"
              % (proc.returncode, proc.stdout, proc.stderr))

bak = dest / 'link.bak'
if not os.path.islink(bak):
    test_fail("KI-72: a SAFE symlink was wrongly dropped from the backup area "
              "under --safe-links (%s missing or not a symlink) -- the fix is "
              "over-blocking." % bak)
if os.readlink(bak) != 'keep.txt':
    test_fail("KI-72: safe backup symlink points to %r, expected 'keep.txt'"
              % os.readlink(bak))

print("safe-links-backup: --safe-links drops an escaping symlink from the "
      "backup area, preserves a safe one, while a plain --backup preserves both")

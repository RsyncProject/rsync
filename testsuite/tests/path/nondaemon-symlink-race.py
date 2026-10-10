#!/usr/bin/env python3

import os
import subprocess

from harness.rsync import (
    SCRATCHDIR, find_attacker_uid, rmtree, rsync_argv, test_fail, test_skipped,
)

if os.geteuid() != 0:
    test_skipped("requires root to plant a backup-dir symlink owned by a non-self uid",
                 capability='cross_uid')
ATT_UID = find_attacker_uid()
if ATT_UID is None:
    test_skipped("no untrusted-uid user available for cross-uid plant", capability='cross_uid')

dest = SCRATCHDIR / 'dest'
outside = SCRATCHDIR / 'outside'
src = SCRATCHDIR / 'src_files'
for d in (dest, outside, src):
    rmtree(d)
    d.mkdir(parents=True)

(dest / 'foo').write_text("INSIDE_TREE_DATA\n")

os.symlink(str(outside), dest / 'bdir')
os.lchown(dest / 'bdir', ATT_UID, ATT_UID)

(src / 'foo').write_text("NEW_PUSHED_DATA\n")

subprocess.run(
    rsync_argv('-t', '--backup', '--backup-dir=bdir', f'{src}/foo', f'{dest}/'),
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
)

if os.path.lexists(outside / 'foo'):
    try:
        got = (outside / 'foo').read_text().strip()
    except OSError:
        got = '<unreadable>'
    test_fail("non-daemon transfer escaped the tree: backup of dest/foo landed "
              f"in {outside}/foo (content: {got}); the receiver followed the "
              "planted backup-dir symlink instead of confining beneath the dest")

leaked = os.listdir(outside)
if leaked:
    test_fail(f"unexpected files escaped the destination tree into {outside}: {leaked}")

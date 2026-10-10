#!/usr/bin/env python3

import os
import subprocess

from harness.mutation import find_attacker_uid
from harness.rsync import SCRATCHDIR, rmtree, rsync_argv, test_fail, test_skipped

if os.geteuid() != 0:
    test_skipped("requires root to plant a backup-dir symlink owned by a non-self uid",
                 capability='cross_uid')
ATT_UID = find_attacker_uid()
if ATT_UID is None:
    test_skipped("no untrusted-uid user available for cross-uid plant", capability='cross_uid')

NFILES = 8

base = SCRATCHDIR / 'destbackup'
src = base / 'src'
dest = base / 'dest'
outside = base / 'outside'

rmtree(base)
src.mkdir(parents=True)
dest.mkdir(parents=True)
for i in range(NFILES):
    (src / f'f{i}').write_text("NEW-PUSHED-CONTENT\n")
    (dest / f'f{i}').write_text("OLD\n")
outside.mkdir(parents=True)
os.symlink('../outside', dest / 'bdir')
os.lchown(dest / 'bdir', ATT_UID, ATT_UID)

subprocess.run(
    rsync_argv('-a', '--backup', '--backup-dir=bdir', f'{src}/', f'{dest}/'),
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

leaked = []
for root, _dirs, files in os.walk(outside):
    leaked += [os.path.join(root, f) for f in files]
if leaked:
    test_fail(
        "backup escaped the destination tree via the planted --backup-dir "
        f"symlink: {sorted(leaked)} were created outside the tree. The secure "
        "resolver failed to confine the backup write.")

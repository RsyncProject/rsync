#!/usr/bin/env python3

import os
import pwd
import subprocess

from harness.rsync import SCRATCHDIR, rmtree, rsync_argv, test_fail, test_skipped

if os.geteuid() != 0:
    test_skipped("requires root to plant a symlink owned by a non-self uid "
                 "(simulates the 'root runs `rsync -a /home /backup`' case)",
                 capability='cross_uid')

NOBODY_UID = None
for name in ('nobody', 'nfsnobody', 'daemon'):
    try:
        u = pwd.getpwnam(name).pw_uid
        if u != 0 and u != os.geteuid():
            NOBODY_UID = u
            break
    except KeyError:
        continue
if NOBODY_UID is None:
    test_skipped("no untrusted-uid user available for cross-uid plant", capability='cross_uid')

base = SCRATCHDIR / 'filtermerge'
src = base / 'src'
dest = base / 'dest'
outside = base / 'outside'

rmtree(base)
(src / 'sub').mkdir(parents=True)
(src / 'sub' / 'canary_file').write_text("would-be-transferred\n")

outside.mkdir(parents=True)
(outside / 'attacker_target').write_text("canary_file\n")

plant = src / 'sub' / '.cvsignore'
os.symlink('../../outside/attacker_target', plant)
os.lchown(plant, NOBODY_UID, NOBODY_UID)

dest.mkdir(parents=True)

subprocess.run(
    rsync_argv('-aC', f'{src}/', f'{dest}/'),
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

if not (dest / 'sub' / 'canary_file').exists():
    test_fail(
        f'.cvsignore symlink owned by uid {NOBODY_UID} excluded '
        'src/sub/canary_file through an outside target'
    )

#!/usr/bin/env python3

import os
import pwd
import subprocess

from harness.rsync import SCRATCHDIR, rmtree, rsync_argv, test_fail, test_skipped

if os.geteuid() != 0:
    test_skipped("requires root to plant a symlink owned by a non-root, "
                 "non-self uid (the attacker simulation)", capability='cross_uid')

NOBODY_UID = None
for name in ('nobody', 'nfsnobody', 'daemon'):
    try:
        NOBODY_UID = pwd.getpwnam(name).pw_uid
        if NOBODY_UID != 0 and NOBODY_UID != os.geteuid():
            break
    except KeyError:
        continue
if NOBODY_UID is None or NOBODY_UID == 0 or NOBODY_UID == os.geteuid():
    test_skipped("no untrusted-uid user available for cross-uid plant "
                 "(tried nobody, nfsnobody, daemon)", capability='cross_uid')

base = SCRATCHDIR / 'logfile'
src = base / 'src'
dest = base / 'dest'
plants = base / 'plants'

rmtree(base)
src.mkdir(parents=True)
(src / 'f').write_text("payload\n")
dest.mkdir(parents=True)
plants.mkdir(parents=True)

sentinel = plants / 'sentinel_leaf'
sentinel.write_text("LEAF_SENTINEL_INITIAL\n")
os.chmod(sentinel, 0o600)

leaf_path = plants / 'log_leaf'
os.symlink(sentinel, leaf_path)
os.lchown(leaf_path, NOBODY_UID, NOBODY_UID)

subprocess.run(
    rsync_argv('-a', '--log-file=' + str(leaf_path), f'{src}/', f'{dest}/'),
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

leaf_after = sentinel.read_text()
if leaf_after != "LEAF_SENTINEL_INITIAL\n":
    test_fail(
        f"--log-file followed a planted LEAF symlink (owned by uid "
        f"{NOBODY_UID}): {sentinel} changed from 'LEAF_SENTINEL_INITIAL' to "
        f"{leaf_after!r}. The privileged rsync appended its log into the "
        f"attacker-chosen file. Fix: refuse planted symlinks at --log-file.")

real_target = plants / 'parent_real_target'
real_target.mkdir()

parent_plant = plants / 'parent_link'
os.symlink(real_target, parent_plant)
os.lchown(parent_plant, NOBODY_UID, NOBODY_UID)

log_via_parent = parent_plant / 'rsync.log'
subprocess.run(
    rsync_argv('-a', '--log-file=' + str(log_via_parent), f'{src}/', f'{dest}/'),
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

outside_log = real_target / 'rsync.log'
if outside_log.exists():
    test_fail(
        f'--log-file traversed untrusted uid {NOBODY_UID} parent symlink '
        f'{parent_plant} -> {real_target} and created {outside_log}')

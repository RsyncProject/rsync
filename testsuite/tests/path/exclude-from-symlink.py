#!/usr/bin/env python3

import os
import pwd
import subprocess

from harness.rsync import SCRATCHDIR, rmtree, rsync_argv, test_fail, test_skipped

if os.geteuid() != 0:
    test_skipped("requires root to plant a symlink owned by a non-self uid "
                 "(the attacker simulation)", capability='cross_uid')

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

base = SCRATCHDIR / 'excludefrom'
src = base / 'src'
dest = base / 'dest'
outside = base / 'outside'

rmtree(base)
src.mkdir(parents=True)
(src / 'canary_file').write_text("would-be-transferred\n")
(src / 'other_file').write_text("noise\n")
dest.mkdir(parents=True)
outside.mkdir(parents=True)

(outside / 'exclude_list').write_text("canary_file\n")

def run_excludefrom(plant_path):
    rmtree(dest)
    dest.mkdir(parents=True)
    return subprocess.run(
        rsync_argv('-a', '--exclude-from=' + str(plant_path),
                   f'{src}/', f'{dest}/'),
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)

plants = base / 'plants'
plants.mkdir()

leaf_plant = plants / 'exclude_leaf'
os.symlink(outside / 'exclude_list', leaf_plant)
os.lchown(leaf_plant, NOBODY_UID, NOBODY_UID)

leaf = run_excludefrom(leaf_plant)
canary = dest / 'canary_file'
if leaf.returncode == 0 and not canary.exists():
    test_fail(
        f"--exclude-from followed a planted LEAF symlink (owned by uid "
        f"{NOBODY_UID}): rsync read the symlink's target as filter rules and "
        f"applied the leaked pattern, dropping src/canary_file from the "
        f"transfer. Fix: refuse planted symlinks at --exclude-from.")

real_target = plants / 'parent_real_target'
real_target.mkdir()
(real_target / 'exclude_list').write_text("canary_file\n")

parent_plant = plants / 'parent_link'
os.symlink(real_target, parent_plant)
os.lchown(parent_plant, NOBODY_UID, NOBODY_UID)

parent = run_excludefrom(parent_plant / 'exclude_list')
canary = dest / 'canary_file'
if parent.returncode == 0 and not canary.exists():
    test_fail(
        f'--exclude-from traversed untrusted uid {NOBODY_UID} parent symlink '
        f'{parent_plant} -> {real_target} and excluded canary_file')

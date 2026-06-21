#!/usr/bin/env python3
# Destination chdir symlink defense (cross-uid) -- ABSOLUTE destination.
#
# A non-daemon receiver chdir()s into the operator-named destination.  change_dir()
# resolves it with open_no_attacker_symlinks: it follows the operator's/root's
# OWN symlinked dest (the /backup->/mnt/disk admin pattern) but REFUSES one owned
# by another uid, closing the dest-chdir TOCTOU -- an attacker who races the named
# destination component from a directory to a symlink->outside can no longer move
# the receiver's CWD, and every file it then creates, out of the tree.  The
# ownership check is timing-independent, so a static attacker-owned plant subsumes
# the race.
#
# Cross-uid is the real threat: root runs `rsync -a .../dest/sub/` over a directory
# where an unprivileged user planted the `sub` component.  Requires root.  This is
# the absolute-destination variant; symlink-race-relative-dest is the companion.

import os
import pwd
import subprocess

from rsyncfns import SCRATCHDIR, rmtree, rsync_argv, test_fail, test_skipped

if os.geteuid() != 0:
    test_skipped("requires root to plant a symlink owned by a non-self uid "
                 "(simulates 'root runs rsync' over an unprivileged user's dir)")

ATT_UID = None
for name in ('nobody', 'nfsnobody', 'daemon'):
    try:
        u = pwd.getpwnam(name).pw_uid
        if u != 0 and u != os.geteuid():
            ATT_UID = u
            break
    except KeyError:
        continue
if ATT_UID is None:
    test_skipped("no untrusted-uid user available for cross-uid plant")

# --- attack: dest/sub is an ATTACKER-owned symlink (absolute path) ------------
base = SCRATCHDIR / 'destchdir'
src = base / 'src'
dest = base / 'dest'
outside = base / 'outside'
rmtree(base)
(src / 'sub').mkdir(parents=True)
for i in range(4):
    (src / 'sub' / f'f{i}').write_text("payload\n")
outside.mkdir(parents=True)
dest.mkdir(parents=True)
os.symlink(outside, dest / 'sub')               # attacker-owned dest component
os.lchown(dest / 'sub', ATT_UID, ATT_UID)

subprocess.run(rsync_argv('-a', f'{src}/sub/', f'{dest}/sub/'),
               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

escaped = sorted(p.name for p in outside.iterdir())
if escaped:
    test_fail(
        "destination chdir followed an ATTACKER-owned symlink: the receiver "
        f"wrote files OUTSIDE the tree ({escaped}). change_dir() must refuse a "
        "dest symlink not owned by uid 0 or the euid.")

# --- admin pattern: the operator's own (root-owned) symlinked dest still works -
real = base / 'realsub'
real.mkdir()
os.unlink(dest / 'sub')
os.symlink(real, dest / 'sub')                  # root-owned symlinked dest

subprocess.run(rsync_argv('-a', f'{src}/sub/', f'{dest}/sub/'),
               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

if not (real / 'f0').is_file():
    test_fail(
        "a root-owned symlinked destination (the /backup->/mnt/disk admin "
        "pattern) was NOT followed -- the cross-uid defense must still follow "
        "the operator's own symlinked dest.")

#!/usr/bin/env python3
# Relative-destination chdir symlink defense (cross-uid).
#
# A non-daemon receiver chdir()s into the operator-named destination.  change_dir()
# now resolves it (relative or absolute) with open_no_attacker_symlinks: it
# follows the operator's/root's OWN symlinked dest (the /backup->/mnt/disk admin
# pattern) but REFUSES one owned by another uid, closing the dest-chdir TOCTOU --
# an attacker who races the operator-named dest from a directory to a
# symlink->outside can no longer redirect the receiver's writes out of the tree.
# (The ownership check is timing-independent, so a static attacker-owned plant
# subsumes the race.)
#
# Cross-uid is the real threat: root runs `rsync -a src/ dest/` from a directory
# where an unprivileged user planted `dest`.  Requires root.  This is the
# relative-destination variant; symlink-race-dest is the absolute companion.
# Found by Omar Elsayed.

import os
import pwd
import subprocess

from rsyncfns import SCRATCHDIR, rmtree, rsync_argv, test_fail, test_skipped

if os.geteuid() != 0:
    test_skipped("requires root to plant a symlink owned by a non-self uid "
                 "(simulates 'root runs rsync -a src/ dest/' over an "
                 "unprivileged user's directory)")

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


def run(cwd):
    subprocess.run(rsync_argv('-a', 'src/', 'dest/'), cwd=str(cwd),
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


# --- attack: a relative dest that is an ATTACKER-owned symlink must be refused -
base = SCRATCHDIR / 'destchdir-rel'
src = base / 'src'
dest = base / 'dest'
outside = base / 'outside'
rmtree(base)
(src / 'sub').mkdir(parents=True)
for i in range(4):
    (src / 'sub' / f'f{i}').write_text("payload\n")
outside.mkdir(parents=True)
os.symlink(outside, dest)            # operator-named relative dest, planted...
os.lchown(dest, ATT_UID, ATT_UID)    # ...by a non-root attacker

run(base)

escaped = sorted(p.name for p in outside.iterdir())
if escaped:
    test_fail(
        "relative destination chdir followed an ATTACKER-owned symlink: the "
        f"receiver wrote files OUTSIDE the tree ({escaped}). change_dir() must "
        "refuse a relative dest symlink not owned by uid 0 or the euid.")

# --- admin pattern: the operator's own (root-owned) symlinked dest still works -
base2 = SCRATCHDIR / 'destchdir-rel-admin'
src2 = base2 / 'src'
real = base2 / 'realdest'
rmtree(base2)
(src2 / 'sub').mkdir(parents=True)
(src2 / 'sub' / 'f0').write_text("payload\n")
real.mkdir(parents=True)
os.symlink(real, base2 / 'dest')     # root-owned symlinked dest (admin pattern)

run(base2)

if not (real / 'sub' / 'f0').is_file():
    test_fail(
        "a root-owned symlinked destination (the /backup->/mnt/disk admin "
        "pattern) was NOT followed -- the cross-uid defense must still follow "
        "the operator's own symlinked dest.")

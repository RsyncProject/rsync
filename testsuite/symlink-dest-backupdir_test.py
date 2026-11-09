#!/usr/bin/env python3
# Destination config-path (--backup-dir) symlink confinement -- DETERMINISTIC.
#
# An attacker who controls a component of the destination plants a STATIC symlink
# as the backup directory, pointing OUTSIDE the destination tree.  When the
# receiver backs up files it is about to overwrite, a plain resolution writes
# each backup through the symlink, outside the tree (root-owned writes to an
# arbitrary location in the worst case).  The secure resolver must confine the
# backup write beneath the destination.
#
# No race is needed: unlike the bare write path -- where rsync re-creates a
# wrong-typed destination directory, normalising a flipped parent away -- the
# --backup-dir path follows the planted symlink directly, so a static symlink
# suffices.  (--temp-dir / --partial-dir do NOT work as escape vectors: rsync
# normalises them the same way it does the destination directory, so this is the
# one config-path that escapes deterministically.)  This complements the racy
# source/chdir tests and nondaemon-symlink-race (a single-file --backup-dir case)
# by confirming confinement across a small tree.

import os
import subprocess

from rsyncfns import (
    SCRATCHDIR, find_attacker_uid, rmtree, rsync_argv, test_fail, test_skipped,
)

# Operator paths follow a uid0/euid-owned symlink (the operator's own backup
# dir) and refuse a foreign-owned one, so the escape vector is an ATTACKER-owned
# backup-dir symlink with rsync run as root.
if os.geteuid() != 0:
    test_skipped("requires root to plant a backup-dir symlink owned by a non-self uid")
ATT_UID = find_attacker_uid()
if ATT_UID is None:
    test_skipped("no untrusted-uid user available for cross-uid plant")


NFILES = 8

base = SCRATCHDIR / 'destbackup'
src = base / 'src'
dest = base / 'dest'
outside = base / 'outside'

rmtree(base)
# A small set of files the transfer will overwrite (so each triggers a backup).
# --backup-dir is resolved relative to the destination directory, so the planted
# symlink and the transfer target are both at the dest top level.
src.mkdir(parents=True)
dest.mkdir(parents=True)
for i in range(NFILES):
    # Different sizes so the transfer actually overwrites (triggering a backup);
    # equal-size content with matching mtimes would be skipped by the quick check.
    (src / f'f{i}').write_text("NEW-PUSHED-CONTENT\n")
    (dest / f'f{i}').write_text("OLD\n")
outside.mkdir(parents=True)
# Attacker-planted backup directory: a foreign-owned symlink pointing outside
# the dest tree (owned by the attacker uid, not root/the operator).
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
# Nothing landed outside the tree -> the backup write was confined.

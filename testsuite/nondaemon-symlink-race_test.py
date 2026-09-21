#!/usr/bin/env python3
# Non-daemon (local) transfer: parent-component symlink-race confinement.
#
# A NON-daemon transfer -- whose
# most dangerous instance is a root nightly backup, e.g. `rsync -a /src/ /dest/`
# -- writes into a destination tree that contains an attacker-planted parent
# component which is a symlink pointing OUTSIDE the tree.  The receiver must
# confine resolution beneath the operator-named destination root and refuse to
# operate through the escaping symlink.
#
# Before the symlink-race hardening was broadened, the secure resolver was
# gated on `am_daemon && !am_chrooted`, so a plain local/remote-shell transfer
# fell through to bare rename()/link()/open() on full paths and followed the
# planted symlink right out of the tree.  This test pins the broadened gate
# (all non-chrooted receivers) -- it FAILS against that older rsync and PASSES
# once the gate covers non-daemon transfers.
#
# Deterministic (no race window): the escaping symlink is pre-planted as the
# --backup-dir.  When the local transfer overwrites an existing in-tree file it
# backs the old copy up through that backup-dir; pre-fix the backup lands
# OUTSIDE the destination tree, post-fix the secure resolver refuses the
# escaping symlink and nothing leaves the tree.  Runs at any uid (the escape
# target is a sibling the running user can write); root is simply the
# highest-severity instance.

import os
import subprocess

from rsyncfns import (
    SCRATCHDIR, find_attacker_uid, rmtree, rsync_argv, test_fail, test_skipped,
)

# The operator-path policy follows a symlink owned by uid 0 or the euid (the
# operator's OWN backup dir) and refuses a foreign-owned one, so the escape
# vector is an ATTACKER-owned backup-dir symlink with rsync run as root.
if os.geteuid() != 0:
    test_skipped("requires root to plant a backup-dir symlink owned by a non-self uid")
ATT_UID = find_attacker_uid()
if ATT_UID is None:
    test_skipped("no untrusted-uid user available for cross-uid plant")


# A non-daemon receiver resolves its write paths on held dirfds with O_NOFOLLOW
# (race-free by construction on every platform), so a parent-component symlink
# swapped in mid-transfer cannot redirect the write outside the destination tree.


dest = SCRATCHDIR / 'dest'
outside = SCRATCHDIR / 'outside'
src = SCRATCHDIR / 'src_files'    # 'src' is reserved by runtests.py (symlink to srcdir)
for d in (dest, outside, src):
    rmtree(d)
    d.mkdir(parents=True)

# An existing in-tree file the transfer will overwrite (and therefore back up).
(dest / 'foo').write_text("INSIDE_TREE_DATA\n")

# The attacker-planted backup-dir: a foreign-owned symlink pointing outside the
# dest tree (owned by the attacker uid, not root/the operator).
os.symlink(str(outside), dest / 'bdir')
os.lchown(dest / 'bdir', ATT_UID, ATT_UID)

# New content so the push genuinely overwrites foo and triggers the backup.
(src / 'foo').write_text("NEW_PUSHED_DATA\n")

# Plain LOCAL (non-daemon) transfer with a backup-dir relative to the dest.
# The transfer may report an error on the fixed binary (the secure backup is
# refused), so ignore its exit status and judge solely by where files land.
subprocess.run(
    rsync_argv('-t', '--backup', '--backup-dir=bdir', f'{src}/foo', f'{dest}/'),
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
)

# The escape: the in-tree file was backed up out through the bdir symlink into
# the outside-the-tree directory.
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

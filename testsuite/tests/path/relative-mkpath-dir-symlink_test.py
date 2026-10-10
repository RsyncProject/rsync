#!/usr/bin/env python3
# --relative directory-entry creation escapes the tree via make_path().
#
# Companion to relative-mkpath-symlink_test.py for the SECOND make_path() call
# site flagged in the May 2026 follow-up.  When the receiver creates a directory
# entry under --relative and the leaf do_mkdir_at() fails with ENOENT (a parent
# component is missing), generator.c falls back to:
#   make_path(fname, MKP_DROP_NAME | MKP_SKIP_SLASH)   (recv_generator, the
#                                                        directory-creation site)
# and then retries do_mkdir_at().  make_path() builds the parent chain with a
# PLAIN do_mkdir() on the full multi-component path, so a symlink planted at any
# parent component is followed and the new directories are created at the
# symlink's target, OUTSIDE the destination tree.
#
# Trigger: transfer a directory whose mid-level parent is absent under a planted
# parent symlink, with --no-implied-dirs so the mid parent is not pre-created.
# do_mkdir_at(dest/A/B/C) fails ENOENT (B missing under the dest/A symlink),
# make_path() then creates dest/A/B == outside/B, escaping the tree.
#
# Deterministic static plant; any uid; platform-independent.  Fixed: the
# do_mkdir_at() ENOENT fallback's make_path() now builds the parent chain through
# the held-dirfd do_mkdir_at(), confining each component beneath the destination.
# This is a regression guard -- it asserts nothing escapes and fails hard if that
# confinement is ever lost.

import subprocess

from rsyncfns import SCRATCHDIR, rmtree, rsync_argv, test_fail


srcroot = SCRATCHDIR / 'srcroot'
dest = SCRATCHDIR / 'dest'
outside = SCRATCHDIR / 'outside'
for d in (srcroot, dest, outside):
    rmtree(d)
    d.mkdir(parents=True)

# A directory three levels below the implied-dir boundary; 'B' (the mid parent)
# is what make_path must create under the planted dest/A symlink.
(srcroot / 'A' / 'B' / 'C').mkdir(parents=True)
(srcroot / 'A' / 'B' / 'C' / 'file').write_text("PAYLOAD\n")

# Attacker-planted parent symlink pointing OUTSIDE the destination tree.
(dest / 'A').symlink_to(outside)

# -R replicates A/B/C/ under dest; --no-implied-dirs leaves B uncreated, so the
# dir-entry do_mkdir_at() fails ENOENT and the make_path() fallback runs.
subprocess.run(
    rsync_argv('-aR', '--no-implied-dirs', f'{srcroot}/./A/B/C/', f'{dest}/'),
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
)

try:
    leaked = sorted(p.name for p in outside.iterdir())
except OSError:
    leaked = []

if leaked:
    test_fail(
        "--relative make_path() (dir-entry fallback) escaped the destination "
        f"tree: parent creation landed in {outside} ({leaked}) by following a "
        "planted dest/A -> outside symlink -- the do_mkdir_at() ENOENT fallback's "
        "make_path() must stay routed through the held-dirfd do_mkdir_at().")
# No escape -> the dir-entry make_path fallback was confined beneath the dest.

#!/usr/bin/env python3
# --relative implied-parent creation escapes the tree via make_path().
#
# Receiver write escape (scenario 2), in a
# code path the held-dirfd hardening does NOT cover.  Under --relative (-R) the
# receiver re-creates the implied parent directories of each transferred path.
# When a needed parent is missing it calls make_path() (util1.c), which creates
# every missing component with a PLAIN do_mkdir() on the full multi-component
# path -- no secure_relative_open, no held dirfd, no O_NOFOLLOW.  The per-entry
# leaf mkdir was converted to the hardened do_mkdir_at(), but make_path()'s
# parent-chain creation was not, so it still follows a symlink planted at any
# parent component and creates directories at the symlink's target, OUTSIDE the
# destination tree.
#
# Trigger (generator.c recv_generator, the relative_paths && !implied_dirs site):
# pushing a file whose implied parent is absent makes the generator call
#   make_path(fname, MKP_DROP_NAME | MKP_SKIP_SLASH)
# to build the parent chain.  We use --no-implied-dirs so the parents are not
# pre-created as their own (hardened do_mkdir_at) entries, forcing the make_path
# fallback.  An attacker-planted parent symlink (dest/A -> outside) then makes
# make_path create dest/A/B == outside/B and the file lands at outside/B/file.
#
# Deterministic (no race window): the escaping component is pre-planted, exactly
# like nondaemon-symlink-race.  Runs at any uid; root (a nightly `rsync -aR`
# backup over a tree with an attacker-writable component) is the high-severity
# instance.  No RESOLVE_BENEATH gate: make_path() is unconfined on every
# platform, so creating a brand-new directory has no legitimate reason to follow
# a symlink component -- the fix should refuse the escape everywhere.
#
# Fixed: make_path()'s parent-chain creation now goes through the held-dirfd
# do_mkdir_at(), which confines each component beneath the destination, so the
# escape is refused.  This is a regression guard -- it asserts nothing escapes
# and fails hard if that confinement is ever lost.

import subprocess

from rsyncfns import SCRATCHDIR, rmtree, rsync_argv, test_fail


srcroot = SCRATCHDIR / 'srcroot'
dest = SCRATCHDIR / 'dest'
outside = SCRATCHDIR / 'outside'
for d in (srcroot, dest, outside):
    rmtree(d)
    d.mkdir(parents=True)

# A transferred file two levels below the implied-dir boundary (the '/./').
(srcroot / 'A' / 'B').mkdir(parents=True)
(srcroot / 'A' / 'B' / 'file').write_text("PAYLOAD\n")

# The attacker-planted parent: dest/A is a symlink pointing OUTSIDE the tree.
(dest / 'A').symlink_to(outside)

# -R replicates the path after '/./' (A/B/file) under dest; --no-implied-dirs
# forces the make_path() parent-chain fallback rather than per-dir entries.
subprocess.run(
    rsync_argv('-aR', '--no-implied-dirs', f'{srcroot}/./A/B/file', f'{dest}/'),
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
)

# The escape: make_path() resolved dest/A/B through the planted dest/A symlink
# and created the directory (and file) in the outside-the-tree directory.
try:
    leaked = sorted(p.name for p in outside.iterdir())
except OSError:
    leaked = []

if leaked:
    test_fail(
        "--relative make_path() escaped the destination tree: implied-parent "
        f"creation landed in {outside} ({leaked}) by following a planted "
        "dest/A -> outside symlink -- make_path()'s parent-chain creation must "
        "stay routed through the held-dirfd do_mkdir_at().")
# No escape -> make_path confined the implied-parent creation beneath the dest.

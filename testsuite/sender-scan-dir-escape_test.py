#!/usr/bin/env python3
# Source-tree directory-ENUMERATION escape via a parent-component symlink race.
#
# Companion to symlink-race-source (which races the sender's file CONTENT open).
# The sender enumerates each source directory with a plain opendir(fbuf) on the
# full accumulated path (flist.c send_directory) -- no O_NOFOLLOW, not routed
# through the held-dirfd resolver.  scan_dirfd = dirfd(d) only makes the
# per-entry stats safe, not the opendir itself.  So a parent component raced from
# a directory to a symlink->OUTSIDE the transfer root between scan time and the
# recursive opendir makes the sender enumerate an out-of-tree directory and copy
# its entries -- names, metadata and SYMLINK TARGETS -- into the destination,
# escaping the root of the transfer.
#
# Regular-file content stays protected by the confined content open (the
# symlink-race-source fix), so the leak signal here is deliberately a SYMLINK in
# the outside dir whose target is a unique marker: it is transferred purely via
# readlinkat() during the scan (no content open), so its appearance in the
# destination proves the enumeration -- not the content open -- escaped.  A
# uniquely-named out-of-tree subdir/file is also checked as a secondary signal.
#
# The escape needs a genuine TOCTOU (a STATIC symlink parent is lstat'd and
# recorded as a symlink, never descended), so a flipper process swaps the
# component directory <-> symlink while rsync runs, retried up to RACE_TIMEOUT.
# Any uid: the attacker owns the source subtree it races.  FAILS on the unfixed
# tree (the out-of-tree symlink target lands in dest); PASSES once the
# enumeration is confined beneath the transfer root.

import os
import subprocess
import time

from rsyncfns import (
    SCRATCHDIR, race_budget, rmtree, rsync_argv, rsync_supports,
    start_path_flipper, stop_flipper, test_fail,
)

MARKER = "XFIL-OUTSIDE-SYMLINK-TARGET-do-not-copy"
NKEEP = 64

base = SCRATCHDIR / 'scan-dir-escape'
src = base / 'src'
outside = base / 'outside'
dest = base / 'dest'

rmtree(base)

# Real-directory state of the flipping component, with in-tree entries whose
# names never appear outside (so an out-of-tree name in dest is unambiguous).
realsub = src / '.realsub'
realsub.mkdir(parents=True)
for i in range(NKEEP):
    (realsub / f'keep{i}').write_text("in-tree\n")

# The out-of-tree target the attacker points the raced component at.  Its
# entries have distinctive names and the symlink carries the marker target.
outside.mkdir(parents=True)
os.symlink(MARKER, outside / 'xfil_link')          # target leaks via readlinkat
(outside / 'xfil_dir').mkdir()                      # name leaks via getdents
(outside / 'xfil_dir' / 'inner').write_text("x\n")
(outside / 'xfil_file').write_text("x\n")           # name/metadata leaks

# 'sub' starts as the real directory; 'evil' is the escaping symlink.  The
# flipper swaps them so 'sub' alternates directory <-> symlink->../outside.
os.rename(realsub, src / 'sub')
os.symlink('../outside', src / 'evil')
dest.mkdir(parents=True)


def escaped():
    """Return a description of any out-of-tree entry that reached dest/sub."""
    d = dest / 'sub'
    if d.is_symlink() or not d.is_dir():
        return None
    link = d / 'xfil_link'
    if link.is_symlink() and os.readlink(link) == MARKER:
        return f"symlink target leaked: dest/sub/xfil_link -> {MARKER}"
    for name in ('xfil_dir', 'xfil_file', 'xfil_link'):
        if (d / name).exists() or (d / name).is_symlink():
            return f"out-of-tree name leaked: dest/sub/{name}"
    return None


# --no-inc-recursive widens the window: the whole tree is scanned (recording
# sub/ as a directory) before the recursive opendir of sub/, giving the flipper
# the scan phase to flip sub to a symlink.  The escape exists with incremental
# recursion too; this makes it reliably winnable within the budget.  Skip the
# flag on implementations that lack it (they default to the safer behaviour).
extra = ['--no-inc-recursive'] if rsync_supports('--no-inc-recursive') else []

flip = start_path_flipper(src / 'sub', src / 'evil')
leak = None
deadline = time.monotonic() + race_budget()
try:
    while time.monotonic() < deadline and leak is None:
        rmtree(dest)
        dest.mkdir(parents=True)
        subprocess.run(rsync_argv('-a', *extra, f'{src}/', f'{dest}/'),
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        leak = escaped()
finally:
    stop_flipper(flip)

if leak is not None:
    test_fail(
        "source-tree directory-enumeration TOCTOU: the sender followed a "
        "parent-component symlink it should have confined and copied an "
        f"out-of-tree entry into the output ({leak}).  The directory scan's "
        "opendir(fbuf) (flist.c send_directory) must resolve beneath the "
        "transfer root, like the content open.")
print("sender-scan-dir-escape: directory enumeration stayed within the transfer root")

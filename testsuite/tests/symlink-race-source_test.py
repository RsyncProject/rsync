#!/usr/bin/env python3
# Source-tree parent-component symlink race (TOCTOU) -- scenarios 1 & 3.
#
# An unprivileged user who controls a subtree of the SOURCE races a parent
# directory component between a real directory and a symlink pointing OUTSIDE the
# source tree.  A higher-authority rsync (a root nightly backup, or a victim user
# copying the attacker's tree) that saw the component as a directory at scan time
# but follows the flipped symlink when it opens a file under it then reads a file
# it should not and copies its contents into the output.
#
# This is the source-side counterpart of nondaemon-symlink-race, which uses
# --backup-dir as a deterministic, race-free RECEIVER primitive.  The pure
# source-read path has no operator-config component the attacker controls:
# rsync's directory descent lstat()s every component with O_NOFOLLOW, so a STATIC
# symlink parent is detected and copied as a symlink, never followed.  The escape
# needs a genuine TOCTOU -- the component must be a directory when rsync scans it
# (so its files enter the file list) and a symlink when rsync opens those files
# -- so we run a flipping thread and retry for up to RACE_TIMEOUT seconds.
#
# Status: FIXED.  For default symlink handling the sender now opens each file's
# content through the confined held-ancestor-dirfd stack anchored at the transfer
# root, so a parent component flipped to a symlink->outside is refused.  The
# symlink-following modes (-L/--copy-links/--copy-unsafe-links/-k) still follow,
# and --insecure-links restores the legacy open.  The test asserts the secret
# does NOT leak: a leak is a hard FAIL.

import os
import platform
import subprocess
import time

from rsyncfns import (
    SCRATCHDIR, race_budget, rmtree, rsync_argv, rsync_supports,
    start_path_flipper, stop_flipper, test_fail, test_xfail,
)

_CYGWIN = platform.system().startswith('CYGWIN')

SECRET = "OUTSIDE-SECRET-do-not-copy"
NFILES = 64

base = SCRATCHDIR / 'srcrace'
src = base / 'src'
outside = base / 'outside'
dest = base / 'dest'

rmtree(base)
# The real-directory state of the flipping component, with in-tree dummy files.
realsub = src / '.realsub'
realsub.mkdir(parents=True)
for i in range(NFILES):
    (realsub / f'loot{i}').write_text("in-tree dummy\n")
# The out-of-tree target the attacker wants rsync to read, under the same names.
outside.mkdir(parents=True)
for i in range(NFILES):
    (outside / f'loot{i}').write_text(SECRET + "\n")
# 'sub' starts as the real directory; 'evil' is the escaping symlink. The flipper
# swaps them so 'sub' alternates directory <-> symlink->../outside.
os.rename(realsub, src / 'sub')
os.symlink('../outside', src / 'evil')
dest.mkdir(parents=True)


def leaked():
    d = dest / 'sub'
    if d.is_symlink() or not d.is_dir():
        return False
    for f in d.iterdir():
        try:
            if f.read_text().strip() == SECRET:
                return True
        except OSError:
            pass
    return False


# --no-inc-recursive widens the TOCTOU window: rsync scans the whole tree
# first (recording sub/ as a directory) and only then opens the files under
# it, so the flipper has the entire read phase -- 64 separate opens of
# sub/* -- to flip sub to a symlink. The disclosure exists with incremental
# recursion too; this just makes the race reliably winnable within the
# budget rather than depending on lucky scan/read interleaving.
#
# Pass the flag only when the rsync binary accepts it: implementations
# without incremental recursion (gokrazy/rsync targets protocol 27, no
# inc-recursive at all) reject --no-inc-recursive as an unknown option,
# but they already default to the safer pre-inc-recursive behaviour.
extra = ['--no-inc-recursive'] if rsync_supports('--no-inc-recursive') else []

flip = start_path_flipper(src / 'sub', src / 'evil')
won = False
deadline = time.monotonic() + race_budget()
try:
    while time.monotonic() < deadline:
        rmtree(dest)
        dest.mkdir(parents=True)
        subprocess.run(rsync_argv('-a', *extra, f'{src}/', f'{dest}/'),
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if leaked():
            won = True
            break
finally:
    stop_flipper(flip)

if won:
    if _CYGWIN:
        # Cygwin emulates symlinks as special files and its open()/path
        # resolution does not enforce the per-component O_NOFOLLOW walk the
        # confined sender open relies on, so the parent-flip leak is still
        # observed here.  The native POSIX symlink-race tests are likewise not
        # enforced on Cygwin (see cygwin-build.yml); track this as a documented
        # Cygwin platform residual rather than a hard failure.
        test_xfail(
            f"cygwin: source-tree parent-flip race still leaks ({SECRET!r}); the "
            "confined sender open is compiled in but Cygwin's symlink emulation "
            "does not enforce it -- documented platform residual.")
    test_fail(
        f"source-tree TOCTOU: content from outside the source tree ({SECRET!r}) "
        "was copied into the output -- the sender followed a parent-component "
        "symlink it should have confined.")
# No leak within the race budget -> the source-side resolution was confined.

#!/usr/bin/env python3
# Non-daemon (local) --backup-dir parent-component symlink-race confinement.
#
# Companion to nondaemon-symlink-race (which pins the DETERMINISTIC leaf case:
# the whole --backup-dir is a pre-planted escaping symlink).  Here the escaping
# symlink is a PARENT component of the backup path that an attacker flips
# dir<->symlink UNDER A RACE:
#
#     rsync -a -b --chmod=777 --backup-dir=backup src/ dest/
#
# Differing files in dest/sub/ are backed up to backup/sub/.  A concurrent
# flipper swaps backup/sub between a real directory and a symlink -> outside.
# If the receiver resolves the backup path while "sub" is the symlink and
# renames the old dest file through it, the (0777) backup lands in outside/,
# clobbering a sentinel there -- an escape.  The static parent-component case
# does NOT reproduce (a pre-existing symlink-to-dir is handled without escape);
# only the race window exposes it, so this case needs the flipper.
#
# The operator-path policy follows a symlink owned by uid 0 or the euid (the
# operator's OWN backup dir) and refuses a foreign-owned one, so the boundary is
# an ATTACKER-owned backup symlink with rsync run as root.  The held-dirfd /
# ownership-walk resolver must refuse it on every flip, not just statically.
#
# RED on stock rsync (the pre-resolver code follows the parent symlink under the
# race); GREEN once make_backup resolves through the operator-path resolver.
# --insecure-links is the documented opt-out and is exercised by the matrix
# tests, not here.

import os
import stat
import subprocess
import time

from rsyncfns import (
    SCRATCHDIR, race_budget, find_attacker_uid,
    rmtree, rsync_argv, start_c_flipper, stop_flipper, test_fail, test_skipped,
)

# Cross-uid plant needs root: only root can own a symlink by a foreign uid and
# thereby make it "not the operator's own".  At any other uid the boundary
# cannot be built, so the race would be vacuous.
if os.geteuid() != 0:
    test_skipped("requires root to plant a backup-dir symlink owned by a non-self uid")
ATT_UID = find_attacker_uid()
if ATT_UID is None:
    test_skipped("no untrusted-uid user available for cross-uid plant")

NFILES = 95

base = SCRATCHDIR / 'bdir-race'
src = base / 'src'
dest = base / 'dest'
backup = base / 'backup'
outside = base / 'outside'
sub = backup / 'sub'            # the parent the attacker flips dir<->symlink
sublink = backup / '.sublink'   # symlink -> outside, swapped in for `sub`


def build():
    rmtree(base)
    for d in (src / 'sub', dest / 'sub', backup, outside):
        d.mkdir(parents=True)
    for i in range(NFILES):
        (src / 'sub' / f'f{i}').write_text('payload\n')
        os.chmod(src / 'sub' / f'f{i}', 0o777)
        (dest / 'sub' / f'f{i}').write_text('different\n')
        os.chmod(dest / 'sub' / f'f{i}', 0o777)
        # Out-of-tree sentinels at a distinctive 0600: a 0777 here means a
        # backed-up dest file escaped through the flipped symlink.
        (outside / f'f{i}').write_text('do-not-touch\n')
        os.chmod(outside / f'f{i}', 0o600)
    sub.mkdir()
    os.symlink(str(outside), sublink)
    os.lchown(sublink, ATT_UID, ATT_UID)   # foreign-owned: must be refused


def push():
    subprocess.run(
        rsync_argv('-a', '-b', '--chmod=777', f'--backup-dir={backup}',
                   f'{src}/', f'{dest}/'),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def escaped() -> bool:
    # Any out-of-tree sentinel turned 0777 == a backup escaped through the symlink.
    with os.scandir(outside) as it:
        for e in it:
            if e.is_file() and stat.S_IMODE(e.stat().st_mode) == 0o777:
                return True
    return False


# ---- positive control: with `sub` a plain dir, a normal push really does back
# up into backup/sub/, so the race below isn't vacuous. ----------------------
build()
push()
if not (sub / 'f0').is_file():
    test_fail("positive control: a normal push did not create backup/sub/f0 -- "
              "backups are not happening, so the race scenario would be vacuous")
if escaped():
    test_fail("positive control: a sentinel was already 0777 before the race")


# ---- the race --------------------------------------------------------------
# Flip backup/sub between the real dir and the attacker-owned symlink->outside.
# The swap is name-only renames, so the symlink keeps its foreign owner; the
# resolver must refuse it on every flip it happens to observe.  The compiled
# flipper (renameat2 EXCHANGE) flips fast enough to win the window reliably even
# on a slow journaled fs; it degrades to the Python flipper where no compiler is.
flip = start_c_flipper(sub, sublink)
try:
    deadline = time.monotonic() + race_budget(10.0)
    while time.monotonic() < deadline:
        # Re-dirty the dest files so every push triggers fresh backups.
        for i in range(80):
            (dest / 'sub' / f'f{i}').write_text('changed\n')
        push()
        if escaped():
            test_fail(
                "backup-dir parent symlink race: a 0777 dest file was backed up "
                f"into {outside} through the flipped backup/sub symlink -- "
                "make_backup followed a foreign-owned parent component (escape).")
finally:
    stop_flipper(flip)

# No escape within the race budget.

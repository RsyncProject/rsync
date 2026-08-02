#!/usr/bin/env python3
# Non-daemon (local) --backup-dir parent-component symlink-race confinement, for
# the case where the backed-up item is a symlink (do_symlink_at's operator path).
#
# High-frequency race variant: a native C flipper swaps the backup parent
# component (backup/sub) with an attacker-owned symlink to an outside directory
# under a live transfer.  The ownership walk must refuse the foreign-owned
# component so no backup symlink is ever created outside the backup tree.

import os
import subprocess
import time

from rsyncfns import (
    SCRATCHDIR, race_budget, find_attacker_uid, rmtree,
    start_c_flipper, stop_flipper, test_fail, test_skipped,
)

if os.geteuid() != 0:
    test_skipped("requires root to plant a backup-dir symlink owned by a non-self uid")

ATT_UID = find_attacker_uid()
if ATT_UID is None:
    test_skipped("no untrusted-uid user available for cross-uid plant")

# Many files widen rsync's per-file backup window so the background flipper has
# more chances to land the parent swap during a backup operation.
NFILES = 95
base = SCRATCHDIR / 'bdir-race'
src = base / 'src'
dest = base / 'dest'
backup = base / 'backup'
outside = base / 'outside'
sub = backup / 'sub'
sublink = backup / '.sublink'


def build():
    """Reset the workspace.  Call only while the flipper is stopped."""
    rmtree(base)
    for d in (src / 'sub', dest / 'sub', backup, outside):
        d.mkdir(parents=True, exist_ok=True)

    # Distinct source and destination symlink values so each transfer overwrites
    # the destination symlink and thus triggers a backup of the old one.
    for i in range(NFILES):
        (src / 'sub' / f'f{i}').symlink_to('test')
        (dest / 'sub' / f'f{i}').symlink_to('test2')

    # The attacker-owned parent-swap target: a symlink to the outside directory.
    os.symlink(str(outside), sublink)
    os.lchown(sublink, ATT_UID, ATT_UID)
    sub.mkdir(parents=True, exist_ok=True)


def push():
    """Runs a blocking local rsync push invocation."""
    return subprocess.run(
        ['./rsync', '-a', '-b', f'--backup-dir={backup}',
         f'{src}/', f'{dest}/'],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )


def escaped() -> bool:
    """True if any symlink was created directly in the outside directory."""
    with os.scandir(outside) as it:
        for e in it:
            if e.is_symlink():
                return True
    return False


# Positive control: a clean run (no flipper) must back the old destination
# symlink into the backup tree, proving this transfer shape exercises the backup
# symlink path.  A broken fixture or an rsync that errors early fails here loudly
# instead of passing the race vacuously.
build()
proc = push()
if proc.returncode != 0:
    test_fail(f"positive control: clean --backup-dir run failed (rc={proc.returncode}):\n{proc.stdout or ''}")
if not (sub / 'f0').is_symlink() or os.readlink(sub / 'f0') != 'test2':
    test_fail("positive control: the old destination symlink was not backed up into "
              f"{sub}; the backup symlink path was not exercised")
if escaped():
    test_fail("positive control: a symlink appeared in outside/ during a no-flipper run")


# ---- THE LIVE RACE ---------------------------------------------------------

deadline = time.monotonic() + race_budget(10.0)
flip = None

try:
    while time.monotonic() < deadline:
        # Reset the workspace only while the flipper is quiet, so build()'s own
        # rmtree/mkdir can't race the swapper and drop artifacts in outside/.
        if flip is not None:
            stop_flipper(flip)
            flip = None
        build()
        flip = start_c_flipper(sub, sublink)
        push()

        if escaped():
            test_fail(
                "--backup-dir parent symlink race: a backup symlink escaped into "
                f"{outside}; rsync followed the flipped attacker-owned backup/sub "
                "component instead of refusing it."
            )
finally:
    if flip is not None:
        stop_flipper(flip)

print("operator-path-backup-symlink: backup symlink confined under parent-swap race")

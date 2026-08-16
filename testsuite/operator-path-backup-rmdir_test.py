#!/usr/bin/env python3
# Non-daemon (local) --backup-dir parent-component symlink-race confinement, for
# the case where a backup replaces a pre-existing backup directory (do_rmdir_at's
# operator path: removing backup/sub/subN before writing the backed-up file).
#
# A native C flipper swaps the backup parent component (backup/sub) with an
# attacker-owned symlink to an outside directory during a live transfer.  The
# ownership walk must refuse the foreign-owned component so the rmdir never
# escapes to remove a directory outside the backup tree.

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

# Many files widen the per-file backup window so the flipper has more chances to
# land the parent swap during a backup rmdir.
NFILES = 50
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

    for i in range(NFILES):
        # The backup target pre-exists as a directory; backing up the old dest
        # file over it forces rsync to rmdir backup/sub/subN first.
        (sub / f'sub{i}').mkdir(parents=True, exist_ok=True)
        # A sentinel directory outside the tree; if the rmdir follows the flipped
        # parent symlink, outside/subN gets removed.
        (outside / f'sub{i}').mkdir(exist_ok=True)
        (src / 'sub' / f'sub{i}').write_text('payload')
        (dest / 'sub' / f'sub{i}').write_text('different')

    os.symlink(str(outside), sublink)
    os.lchown(sublink, ATT_UID, ATT_UID)


def push():
    """Runs a blocking local rsync push invocation."""
    return subprocess.run(
        ['./rsync', '-a', '-b', f'--backup-dir={backup}',
         f'{src}/', f'{dest}/'],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )


def escaped() -> bool:
    """True if any outside sentinel directory was removed (escaped rmdir)."""
    for i in range(NFILES):
        if not (outside / f'sub{i}').is_dir():
            return True
    return False


# Positive control: a clean run (no flipper) must back the old dest file over the
# pre-existing backup directory, proving this transfer shape exercises the backup
# rmdir path.  A broken fixture or an rsync that errors early fails here loudly
# instead of passing the race vacuously.
build()
proc = push()
if proc.returncode != 0:
    test_fail(f"positive control: clean --backup-dir run failed (rc={proc.returncode}):\n{proc.stdout or ''}")
if not (sub / 'sub0').is_file() or (sub / 'sub0').read_text() != 'different':
    test_fail("positive control: the old destination file was not backed up over the "
              "pre-existing backup directory; the backup rmdir path was not exercised")
if escaped():
    test_fail("positive control: an outside sentinel dir vanished during a no-flipper run")


# ---- THE LIVE RACE ---------------------------------------------------------

deadline = time.monotonic() + race_budget(10.0)
flip = None

try:
    while time.monotonic() < deadline:
        if flip is not None:
            stop_flipper(flip)
            flip = None
        build()
        flip = start_c_flipper(sub, sublink)
        push()

        if escaped():
            test_fail(
                "--backup-dir parent symlink race: a backup rmdir escaped the tree, "
                f"removing a sentinel directory under {outside}; rsync followed the "
                "flipped attacker-owned backup/sub component instead of refusing it."
            )
finally:
    if flip is not None:
        stop_flipper(flip)

print("operator-path-backup-rmdir: backup rmdir confined under parent-swap race")

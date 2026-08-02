#!/usr/bin/env python3
# --backup-dir parent-component symlink-race confinement for the OWNERSHIP set,
# not the create.  operator-path-backup-symlink covers the create side (a backup
# symlink must not be written outside the backup tree); this covers what
# set_file_attrs() does to the item afterwards.
#
# make_backup() recreates the item at the backup name and then calls
# set_file_attrs(buf, ..., ATTRS_OPERATOR_PATH).  A regular/dir/fifo leaf is
# pinned by op_pin and its metadata driven off that fd, but a SYMLINK leaf never
# enters op_pin (there is no O_NOFOLLOW open of a symlink), so the chown falls
# through to the path-based wrapper on the full operator path.  Unless that
# wrapper resolves through the ownership walk, a parent component flipped to an
# attacker-owned symlink redirects the lchown out of the backup tree and retags
# a victim inode as the attacker's -- an ownership-transfer primitive, and the
# trust laundering that then defeats the walk on any later pass.
#
# Reaching the recreate path at all needs the backup dir on ANOTHER filesystem:
# on one filesystem make_backup() hard-links or renames the item across and
# never calls set_file_attrs().  So the whole fixture lives on tmpfs.
#
# A statically planted symlink is not enough either -- rsync's own backup-dir
# validation deletes a non-directory component before using it -- so the plant
# has to be a live flip, as in the sibling test.

import os
import subprocess
import time

from rsyncfns import (
    SCRATCHDIR, race_budget, find_attacker_uid, rmtree, makepath,
    start_c_flipper, stop_flipper, test_fail, test_skipped,
)

if os.geteuid() != 0:
    test_skipped("requires root to own a symlink by a foreign uid and to chown backups")

ATT_UID = find_attacker_uid()
if ATT_UID is None:
    test_skipped("no untrusted-uid user available for cross-uid plant")

# The backup dir must be on a different st_dev from the destination, or
# make_backup() renames into it and the set_file_attrs() path never runs.
dest_dev = os.stat(SCRATCHDIR).st_dev
TMPFS = None
for cand in ('/dev/shm', '/run/shm', os.environ.get('TMPDIR', '/tmp')):
    try:
        if os.stat(cand).st_dev != dest_dev and os.access(cand, os.W_OK):
            TMPFS = cand
            break
    except OSError:
        continue
if TMPFS is None:
    test_skipped("no writable cross-device dir (tmpfs) for the --backup-dir EXDEV path")

# Many files widen the per-file backup window so the flipper has more chances to
# land the swap between the recreate and the chown.
NFILES = 95

base = SCRATCHDIR / 'bdir-chown-race'
src = base / 'src'
dest = base / 'dest'

bakroot = os.path.join(TMPFS, 'rsync-bakchown-race')
backup = os.path.join(bakroot, 'backup')
outside = os.path.join(bakroot, 'outside')
sub = os.path.join(backup, 'sub')
sublink = os.path.join(backup, '.sublink')


def build():
    """Reset the workspace.  Call only while the flipper is stopped."""
    rmtree(base)
    subprocess.run(['rm', '-rf', bakroot], check=False)
    makepath(src / 'sub', dest / 'sub')
    os.makedirs(outside, exist_ok=True)
    os.makedirs(backup, exist_ok=True)

    # Distinct source and destination symlink values so each transfer replaces
    # the destination symlink and thus backs the old one up.  The destination
    # symlinks are attacker-owned, so restoring their ownership onto the backup
    # copy REQUIRES an lchown -- without that there is no chown to redirect and
    # the test would pass vacuously.
    for i in range(NFILES):
        (src / 'sub' / f'f{i}').symlink_to('test')
        d = dest / 'sub' / f'f{i}'
        d.symlink_to('test2')
        os.lchown(d, ATT_UID, ATT_UID)

    # Victims: root-owned regular files carrying the names the backup would use
    # if a flipped `sub` redirected the operator path into outside/.
    for i in range(NFILES):
        v = os.path.join(outside, f'f{i}')
        with open(v, 'w') as fh:
            fh.write('victim\n')
        os.chown(v, 0, 0)

    # The attacker-owned parent-swap target.
    os.symlink(outside, sublink)
    os.lchown(sublink, ATT_UID, ATT_UID)
    os.makedirs(sub, exist_ok=True)


def push():
    """Blocking local rsync push that backs up the old destination symlinks."""
    return subprocess.run(
        ['./rsync', '-a', '-b', f'--backup-dir={backup}', f'{src}/', f'{dest}/'],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )


def retagged():
    """Name of a victim in outside/ that stopped being root-owned, or ''.

    A swap killed mid-rename can leave outside/ momentarily odd; anything we
    cannot stat is simply not evidence of a win."""
    try:
        with os.scandir(outside) as it:
            for e in it:
                try:
                    st = e.stat(follow_symlinks=False)
                except OSError:
                    continue
                if st.st_uid != 0 or st.st_gid != 0:
                    return e.name
    except (FileNotFoundError, NotADirectoryError):
        return ''
    return ''


# ---- POSITIVE CONTROL ------------------------------------------------------
# A clean run must (a) back the old destination symlink into the backup tree via
# the cross-device recreate path and (b) carry the attacker ownership onto that
# backup copy -- which is the lchown this test is about.  Without both, the race
# below would be asserting on a code path that never executes.
build()
proc = push()
if proc.returncode != 0:
    test_fail(f"positive control: clean --backup-dir run failed (rc={proc.returncode}):\n{proc.stdout or ''}")

bak0 = os.path.join(sub, 'f0')
if not os.path.islink(bak0) or os.readlink(bak0) != 'test2':
    test_fail(f"positive control: the old destination symlink was not backed up into {sub}; "
              "the cross-device recreate path was not exercised")
st = os.lstat(bak0)
if st.st_uid != ATT_UID:
    test_fail(f"positive control: backup copy {bak0} is uid {st.st_uid}, expected the "
              f"attacker uid {ATT_UID}; set_file_attrs() did not lchown the backup, so "
              "this test would pass vacuously")
if retagged():
    test_fail("positive control: a victim in outside/ changed ownership during a no-flipper run")


# ---- THE LIVE RACE ---------------------------------------------------------
# Flip backup/sub between the real backup directory and the attacker-owned
# symlink to outside/ under a live transfer.  The ownership walk must refuse the
# foreign-owned component, so no victim in outside/ is ever retagged.

deadline = time.monotonic() + race_budget(10.0)
flip = None
try:
    while time.monotonic() < deadline:
        # Reset only while the flipper is quiet, so build()'s rmtree/mkdir
        # cannot race the swapper and drop artifacts in outside/.
        if flip is not None:
            stop_flipper(flip)
            flip = None
        build()
        flip = start_c_flipper(sub, sublink)
        push()

        victim = retagged()
        if victim:
            test_fail(
                "--backup-dir parent symlink race: victim "
                f"{os.path.join(outside, victim)} was retagged away from root; rsync "
                "chowned through the flipped attacker-owned backup/sub component "
                "instead of refusing it."
            )
finally:
    if flip is not None:
        stop_flipper(flip)
    subprocess.run(['rm', '-rf', bakroot], check=False)

print("operator-path-backup-chown: backup ownership confined under parent-swap race")

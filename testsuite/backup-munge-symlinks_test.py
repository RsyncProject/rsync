import os
import subprocess
import time

from rsyncfns import (
    SCRATCHDIR, race_budget, find_attacker_uid, rmtree, rsync_argv,
    start_c_flipper, stop_flipper, test_fail, test_skipped, start_test_daemon,
    write_daemon_conf,
)

PORT = 13334
SYMLINK_PREFIX = "/rsyncd-munged/"

if os.geteuid() != 0:
    test_skipped("requires root to plant a backup-dir symlink owned by a non-self uid")

ATT_UID = find_attacker_uid()
if ATT_UID is None:
    test_skipped("no untrusted-uid user available for cross-uid plant")

SECRET = "NO_ONE_SHOULD_README\n"
NFILES = 80
root = SCRATCHDIR / 'backup-munge'
root.mkdir()
base = root / 'base'
outside = base / 'outside'
src = base / 'src'
dest = base / 'dest'
backup = base / 'backup'
sub = backup / 'sub'
sublink = backup / '.sublink'

secret = root / 'secret'
secret.write_text(SECRET)


def build(dest_target=None):
    """Reset the workspace.  Call only while the flipper is stopped."""
    rmtree(base)
    for d in (src / 'sub', dest / 'sub', backup, outside):
        d.mkdir(parents=True, exist_ok=True)

    if dest_target is None:
        dest_target = f"{secret}"

    for i in range(NFILES):
        (src / 'sub' / f'f{i}').symlink_to('test')
        (dest / 'sub' / f'f{i}').symlink_to(dest_target)
        os.lchown(src / 'sub' / f'f{i}', ATT_UID, ATT_UID)
        os.lchown(dest / 'sub' / f'f{i}', ATT_UID, ATT_UID)

    os.symlink(str(outside), sublink)
    os.lchown(sublink, ATT_UID, ATT_UID)
    sub.mkdir(parents=True, exist_ok=True)


def push(module='m'):
    return subprocess.run(
        rsync_argv('-a', '-b', '--backup-dir=/backup/',
                   f'{src}/', f'rsync://127.0.0.1:{PORT}/{module}/dest/'),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def normalize_sub():
    try:
        if sub.is_symlink() and sublink.is_dir() and not sublink.is_symlink():
            sub.unlink()
            sublink.rename(sub)
    except OSError:
        pass


def root_owned_backup_symlink():
    normalize_sub()
    try:
        if sub.is_symlink() or not sub.is_dir():
            return ""
        with os.scandir(sub) as it:
            for entry in it:
                if entry.is_symlink() and entry.stat(follow_symlinks=False).st_uid == 0:
                    return entry.name
    except (FileNotFoundError, NotADirectoryError):
        return ""
    return ""


def win_backup_race(dest_target):
    """Reset the fixture on every attempt before running the flipper and checking
    for a root-owned backup symlink, so leftovers from an earlier case can never
    short-circuit the loop."""
    deadline = time.monotonic() + race_budget(10.0)
    flip = None
    try:
        while time.monotonic() < deadline:
            build(dest_target=dest_target)
            flip = start_c_flipper(sub, sublink)
            push()
            stop_flipper(flip)
            flip = None

            winner = root_owned_backup_symlink()
            if winner:
                return winner
    finally:
        if flip is not None:
            stop_flipper(flip)
    return ""


conf = write_daemon_conf(
    [
        ('m', {'path': str(base), 'read only': 'no', 'use chroot': 'no'}),
        ('m_munged', {'path': str(base), 'read only': 'no', 'use chroot': 'no', 'munge symlinks': 'yes'}),
    ],
    name='backup-munge-rsyncd.conf',
)
start_test_daemon(conf, PORT)

# 1. Verify normal push with 'munge symlinks = yes' (where recv_file_entry()
# already prepends SYMLINK_PREFIX) does NOT get double-munged by do_symlink_at().
build()
proc = push(module='m_munged')
if proc.returncode != 0:
    test_fail(f"clean m_munged run failed (rc={proc.returncode})")
dest_link_target = os.readlink(dest / 'sub' / 'f0')
if dest_link_target != f"{SYMLINK_PREFIX}test":
    test_fail(
        f"expected single-munged destination symlink {SYMLINK_PREFIX + 'test'!r}, "
        f"got {dest_link_target!r}"
    )

# 2. Positive control for backup path
build()
proc = push()
if proc.returncode != 0:
    test_fail(f"positive control: clean --backup-dir run failed (rc={proc.returncode})")
if not (sub / 'f0').is_symlink() or os.readlink(sub / 'f0') != f'{secret}':
    test_fail(
        "positive control: the old destination symlink was not backed up into "
        f"{sub}; the backup symlink path was not exercised"
    )

# 3. Race test: unmunged symlink on disk and already-munged symlink on disk
# backed up via do_symlink_at() fallback must both end up single-munged on disk.
for label, initial_target, expected_target in (
    ("unmunged", f"{secret}", f"{SYMLINK_PREFIX}{secret}"),
    ("pre-munged", f"{SYMLINK_PREFIX}{secret}-premunged", f"{SYMLINK_PREFIX}{secret}-premunged"),
):
    backed_up_link = win_backup_race(dest_target=initial_target)
    if not backed_up_link:
        test_skipped(f"race ({label}) did not produce a root-owned backup symlink (no atomic flipper?)")

    target = os.readlink(sub / backed_up_link)
    if target.startswith(SYMLINK_PREFIX + SYMLINK_PREFIX):
        test_fail(f"{label} backup symlink was double-munged: {target!r}")
    if target != expected_target:
        test_fail(
            f"{label} root-owned backup symlink target mismatch: got {target!r}, "
            f"expected {expected_target!r}"
        )

# 4. Exact-prefix boundary test ('<' vs '<=' in munge_symlink_target()):
# An on-disk symlink whose target is the exact string "/rsyncd-munged/" must
# not be prefixed a second time when recreated by do_symlink_at().
backed_up_link = win_backup_race(dest_target=SYMLINK_PREFIX)
if not backed_up_link:
    test_skipped("race (exact-prefix) did not produce a root-owned backup symlink (no atomic flipper?)")

target = os.readlink(sub / backed_up_link)
if target != SYMLINK_PREFIX:
    test_fail(
        f"already-munged exact-prefix backup symlink was double-munged or corrupted: "
        f"got {target!r}, expected {SYMLINK_PREFIX!r}"
    )

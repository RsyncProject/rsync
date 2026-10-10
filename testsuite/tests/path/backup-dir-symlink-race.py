#!/usr/bin/env python3

import os
import stat
import subprocess
import time

from harness.mutation import find_attacker_uid, race_budget, start_c_flipper, stop_flipper
from harness.rsync import SCRATCHDIR, rmtree, rsync_argv, test_fail, test_skipped

if os.geteuid() != 0:
    test_skipped("requires root to plant a backup-dir symlink owned by a non-self uid",
                 capability='cross_uid')
ATT_UID = find_attacker_uid()
if ATT_UID is None:
    test_skipped("no untrusted-uid user available for cross-uid plant", capability='cross_uid')

NFILES = 95

base = SCRATCHDIR / 'bdir-race'
src = base / 'src'
dest = base / 'dest'
backup = base / 'backup'
outside = base / 'outside'
sub = backup / 'sub'
sublink = backup / '.sublink'

def build():
    rmtree(base)
    for d in (src / 'sub', dest / 'sub', backup, outside):
        d.mkdir(parents=True)
    for i in range(NFILES):
        (src / 'sub' / f'f{i}').write_text('payload\n')
        os.chmod(src / 'sub' / f'f{i}', 0o777)
        (dest / 'sub' / f'f{i}').write_text('different\n')
        os.chmod(dest / 'sub' / f'f{i}', 0o777)
        (outside / f'f{i}').write_text('do-not-touch\n')
        os.chmod(outside / f'f{i}', 0o600)
    sub.mkdir()
    os.symlink(str(outside), sublink)
    os.lchown(sublink, ATT_UID, ATT_UID)

def push():
    subprocess.run(
        rsync_argv('-a', '-b', '--chmod=777', f'--backup-dir={backup}',
                   f'{src}/', f'{dest}/'),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )

def escaped() -> bool:
    with os.scandir(outside) as it:
        for e in it:
            if e.is_file() and stat.S_IMODE(e.stat().st_mode) == 0o777:
                return True
    return False

build()
push()
if not (sub / 'f0').is_file():
    test_fail("positive control: a normal push did not create backup/sub/f0 -- "
              "backups are not happening, so the race scenario would be vacuous")
if escaped():
    test_fail("positive control: a sentinel was already 0777 before the race")

flip = start_c_flipper(sub, sublink)
try:
    deadline = time.monotonic() + race_budget(10.0)
    while time.monotonic() < deadline:
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

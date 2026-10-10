#!/usr/bin/env python3

import os
import subprocess
import time

from harness.mutation import find_attacker_uid, race_budget, start_c_flipper, stop_flipper
from harness.rsync import SCRATCHDIR, rmtree, rsync_argv, test_fail, test_skipped

if os.geteuid() != 0:
    test_skipped('backup parent race requires root', capability='cross_uid')
attacker_uid = find_attacker_uid()
if attacker_uid is None:
    test_skipped('no untrusted uid is available', capability='cross_uid')

def check(kind, count):
    base = SCRATCHDIR / kind
    source = base / 'source'
    destination = base / 'destination'
    backup = base / 'backup'
    outside = base / 'outside'
    subdirectory = backup / 'sub'
    replacement = backup / '.replacement'

    def build():
        rmtree(base)
        for path in (source / 'sub', destination / 'sub', backup, outside):
            path.mkdir(parents=True)
        subdirectory.mkdir()
        for index in range(count):
            if kind == 'directory':
                (outside / f'item{index}').mkdir()
                (source / 'sub' / f'item{index}').write_text('new content')
                (destination / 'sub' / f'item{index}').write_text('old')
            else:
                (source / 'sub' / f'item{index}').symlink_to('new')
                (destination / 'sub' / f'item{index}').symlink_to('old')
        replacement.symlink_to(outside)
        os.lchown(replacement, attacker_uid, attacker_uid)

    def push():
        return subprocess.run(
            rsync_argv('-a', '--backup', f'--backup-dir={backup}',
                       f'{source}/', f'{destination}/'),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

    def escaped():
        if kind == 'directory':
            return any(not (outside / f'item{index}').is_dir() for index in range(count))
        return any(entry.is_symlink() for entry in os.scandir(outside))

    build()
    result = push()
    if result.returncode:
        test_fail(f'{kind}: backup control failed:\n{result.stdout}')
    saved = subdirectory / 'item0'
    if kind == 'directory' and (not saved.is_file() or saved.read_text() != 'old'):
        test_fail('directory backup path was not exercised')
    if kind == 'symlink' and (not saved.is_symlink() or os.readlink(saved) != 'old'):
        test_fail('symlink backup path was not exercised')
    if escaped():
        test_fail(f'{kind}: control escaped before the race')

    deadline = time.monotonic() + race_budget(10)
    flipper = None
    try:
        while time.monotonic() < deadline:
            if flipper is not None:
                stop_flipper(flipper)
            build()
            flipper = start_c_flipper(subdirectory, replacement)
            push()
            if escaped():
                test_fail(f'{kind}: backup escaped during a parent swap')
    finally:
        if flipper is not None:
            stop_flipper(flipper)

check('directory', 50)
check('symlink', 95)
print('backup operations remain confined during parent swaps')

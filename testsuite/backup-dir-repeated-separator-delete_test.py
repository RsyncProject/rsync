#!/usr/bin/env python3
"""Repeated backup-dir separators must not replace the backup directory."""

import os
import subprocess

from rsyncfns import SCRATCHDIR, makepath, rmtree, rsync_argv, test_fail


OLD = b'old destination bytes\n'
NEW = b'new source bytes with a different length\n'
SENTINEL = b'unrelated backup history\n'
NESTED = b'nested backup history\n'

base = SCRATCHDIR / 'backup-dir-repeated-separator-delete'
rmtree(base)
source = base / 'source'
destination = base / 'destination'
backup = base / 'backup'
makepath(source)
makepath(destination)
makepath(backup / 'nested')

(source / 'file').write_bytes(NEW)
(destination / 'file').write_bytes(OLD)
(backup / 'sentinel').write_bytes(SENTINEL)
(backup / 'nested' / 'history').write_bytes(NESTED)
os.utime(source / 'file', (1_750_000_000, 1_750_000_000))
os.utime(destination / 'file', (1_700_000_000, 1_700_000_000))

proc = subprocess.run(
    rsync_argv('-a', '--backup', f'--backup-dir={backup}//',
               f'{source}/', f'{destination}/'),
    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
)
if proc.returncode != 0:
    test_fail(f'repeated-separator transfer exited {proc.returncode}:\n{proc.stdout}')
if not backup.is_dir():
    test_fail('repeated separator replaced the backup directory itself')
if not (backup / 'file').is_file() or (backup / 'file').read_bytes() != OLD:
    test_fail('old destination was not stored as backup/file')
if not (backup / 'sentinel').is_file() or (backup / 'sentinel').read_bytes() != SENTINEL:
    test_fail('repeated separator deleted unrelated top-level backup history')
if ((backup / 'nested' / 'history').read_bytes() != NESTED):
    test_fail('repeated separator deleted unrelated nested backup history')
if (destination / 'file').read_bytes() != NEW:
    test_fail('new source content was not published at the destination')

print('backup-dir-repeated-separator-delete: backup tree and history preserved')

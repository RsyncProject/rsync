#!/usr/bin/env python3
"""A batch-only run must not remove a source it has not installed."""

import os
import subprocess

from rsyncfns import SCRATCHDIR, makepath, rmtree, rsync_argv, test_fail


base = SCRATCHDIR / 'batch-only-remove-source-regression'
source = base / 'source'
destination = base / 'destination'
rmtree(base)
makepath(source / 'one' / 'two' / 'three')
makepath(destination / 'one' / 'two' / 'three')

source_file = source / 'one' / 'two' / 'three' / 'payload'
destination_file = destination / 'one' / 'two' / 'three' / 'payload'
source_bytes = b'replacement payload\n' * 31
destination_bytes = b'original receiver bytes\n' * 17
source_file.write_bytes(source_bytes)
destination_file.write_bytes(destination_bytes)
source_link_a = source / 'one' / 'two' / 'linked-a'
source_link_b = source / 'one' / 'two' / 'linked-b'
source_link_a.write_bytes(source_bytes)
os.link(source_link_a, source_link_b)
destination_link_a = destination / 'one' / 'two' / 'linked-a'
destination_link_b = destination / 'one' / 'two' / 'linked-b'
destination_link_a.write_bytes(destination_bytes)
destination_link_b.write_bytes(destination_bytes)

batch = base / 'transfer.batch'
write = subprocess.run(
    rsync_argv('-aH', f'--only-write-batch={batch}',
               '--remove-source-files', f'{source}/', f'{destination}/'),
    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
if write.returncode != 0:
    test_fail(f'batch-only transfer exited {write.returncode}:\n{write.stdout}')
if destination_file.read_bytes() != destination_bytes:
    test_fail('--only-write-batch changed the destination before replay')
if (destination_link_a.read_bytes() != destination_bytes
        or destination_link_b.read_bytes() != destination_bytes):
    test_fail('--only-write-batch changed hard-linked destinations before replay')
if not batch.is_file() or batch.stat().st_size <= 4:
    test_fail('--only-write-batch did not create a replayable batch file')
if not source_file.exists():
    test_fail('--only-write-batch --remove-source-files deleted the nested '
              'source before the batch was applied')
if source_file.read_bytes() != source_bytes:
    test_fail('batch-only transfer changed the retained source')
if not source_link_a.exists() or not source_link_b.exists():
    test_fail('--only-write-batch removed a hard-linked source before replay: '
              f'linked-a={source_link_a.exists()} '
              f'linked-b={source_link_b.exists()}')
if source_link_a.stat().st_ino != source_link_b.stat().st_ino:
    test_fail('batch-only transfer broke the retained source hard link')

replay = subprocess.run(
    rsync_argv('-aH', f'--read-batch={batch}', f'{destination}/'),
    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
if replay.returncode != 0:
    test_fail(f'batch replay exited {replay.returncode}:\n{replay.stdout}')
if destination_file.read_bytes() != source_bytes:
    test_fail('the retained batch did not install the source payload')
if (destination_link_a.read_bytes() != source_bytes
        or destination_link_b.read_bytes() != source_bytes):
    test_fail('the retained batch did not install hard-linked payloads')
if destination_link_a.stat().st_ino != destination_link_b.stat().st_ino:
    test_fail('the retained batch did not reproduce the source hard link')
if source_file.read_bytes() != source_bytes:
    test_fail('batch replay changed or removed the source')

print('batch-only-remove-source-regression: source retained until batch replay')

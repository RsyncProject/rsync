#!/usr/bin/env python3

import os
import subprocess

from harness import TestContext, requires, run
from harness.results import test_fail, test_skipped
from harness.rsync import rsync_argv, run_rsync
from harness.xattrs import is_supported


VOLUME = b'1ad3d840-cb2c-42f6-9360-817f1d4163ad'


def mark(path, partition, block, volume=VOLUME):
    os.setxattr(path, 'user.ltfs.volumeUUID', volume)
    os.setxattr(path, 'user.ltfs.partition', partition)
    os.setxattr(path, 'user.ltfs.startblock', block)


@requires(features={'linux_xattr', 'xattr_runtime'}, transports={'pipe'}, min_peer='4.0.0',
          mutates={'filesystem', 'process'}, tags={'transfer', 'regression'})
def test(context: TestContext):
    if not is_supported():
        test_skipped('LTFS ordering requires native Linux xattrs', capability='xattr_runtime')
    source = context.source
    source.mkdir()
    entries = (('a-b1', b'b', b'1'), ('b-a9', b'a', b'9'),
               ('c-a9', b'a', b'9'), ('m-b2', b'b', b'2'), ('z-a1', b'a', b'1'))
    for name, partition, block in entries:
        path = source / name
        path.write_bytes(name.encode('ascii') + b' data\n')
        mark(path, partition, block)
    (source / 'empty').touch()

    dest = context.destination
    args = ('-rt', '--ltfs', '--out-format=%n', f'{source}/', f'{dest}/')
    result = run_rsync(*args, capture_output=True, check=False)
    if result.returncode:
        test_fail(f'LTFS transfer failed: {result.stderr}')
    names = {entry[0] for entry in entries}
    order = [row for row in result.stdout.splitlines() if row in names]
    if order != ['z-a1', 'b-a9', 'c-a9', 'a-b1', 'm-b2']:
        test_fail(f'files were not requested in partition and block order: {order!r}')
    for name in names | {'empty'}:
        if (dest / name).read_bytes() != (source / name).read_bytes():
            test_fail(f'ordered transfer changed the contents of {name}')
    repeat = run_rsync(*args, capture_output=True)
    if any(row in names for row in repeat.stdout.splitlines()):
        test_fail('an unchanged source file was read again')

    refused = context.scratch / 'refused'
    refused.mkdir()
    keep = refused / 'sentinel'
    keep.write_bytes(b'previous data\n')
    record = source / 'a-b1'
    for partition, block, volume in ((b'b', b'1x', VOLUME), (b'c', b'1', VOLUME),
                                     (b'b', b'4611686018427387904', VOLUME),
                                     (b'b', b'1', b'22d69770-f934-42f3-a6c4-e8220ea2571a')):
        mark(record, partition, block, volume)
        result = run_rsync('-rt', '--ltfs', f'{source}/', f'{refused}/',
                           capture_output=True, check=False)
        if (result.returncode == 0 or 'LTFS' not in result.stderr
                or list(refused.iterdir()) != [keep] or keep.read_bytes() != b'previous data\n'):
            test_fail(f'bad LTFS metadata did not stop before mutation: {result.stderr}')
    mark(record, b'b', b'1')
    (source / 'nested').mkdir()
    result = run_rsync('-rt', '--ltfs', f'{source}/', f'{refused}/',
                       capture_output=True, check=False)
    if result.returncode == 0 or 'flat source' not in result.stderr or list(refused.iterdir()) != [keep]:
        test_fail(f'a nested source changed the destination: {result.stderr}')

    for incompatible in ('--checksum', '--delete', '--no-whole-file', '--info=progress2'):
        result = run_rsync('-rt', '--ltfs', incompatible, f'{source}/', f'{refused}/',
                           capture_output=True, check=False)
        if result.returncode != 1 or list(refused.iterdir()) != [keep]:
            test_fail(f'{incompatible}: incompatible operation was not refused')

    for protected in ((), ('-s',)):
        result = subprocess.run(
            rsync_argv('--ltfs', '--server', *protected, '.', str(refused)),
            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 1 or '--ltfs requires a local restore' not in result.stderr:
            test_fail(f'server invocation did not refuse LTFS ordering: {result.stderr}')
    if list(refused.iterdir()) != [keep] or keep.read_bytes() != b'previous data\n':
        test_fail('a refused server invocation changed the destination')


if __name__ == '__main__':
    run(test)

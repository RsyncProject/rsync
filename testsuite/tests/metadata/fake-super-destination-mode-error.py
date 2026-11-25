#!/usr/bin/env python3

import errno
import os
import stat
import subprocess

from harness import TestContext, requires, run
from harness.results import test_fail, test_skipped
from harness.rsync import rsync_argv
from harness.xattrs import is_supported


@requires(features={'linux_interpose', 'xattr_runtime'}, transports={'pipe'}, min_peer='4.0.0',
          mutates={'filesystem', 'process'}, tags={'metadata', 'regression'})
def test(context: TestContext):
    if not is_supported():
        test_skipped('fake-super requires xattrs', capability='xattr_runtime')

    context.source.mkdir()
    new = context.source / 'payload'
    new.write_bytes(b'payload data\n')
    new.chmod(0o644)
    context.destination.mkdir()
    previous = context.destination / 'payload'
    previous.write_bytes(b'previous backup\n')
    previous.chmod(0o600)
    inode = previous.stat().st_ino

    fixture = context.repository / 'testsuite/fixtures/chmod-error.c'
    library = context.scratch / 'chmod-error.so'
    build = subprocess.run(['cc', '-shared', '-fPIC', '-o', str(library), str(fixture), '-ldl'],
                           capture_output=True, text=True, timeout=30)
    if build.returncode:
        test_fail(f'cannot build chmod hook: {build.stderr}')
    marker = context.scratch / 'chmod-failed'
    env = {**os.environ, 'LD_PRELOAD': str(library),
           'RSYNC_TEST_CHMOD_DIR': str(context.destination.resolve()),
           'RSYNC_TEST_CHMOD_MARKER': str(marker), 'RSYNC_TEST_CHMOD_ERRNO': str(errno.EIO),
           'RSYNC_TEST_CHMOD_MODE': '0664'}

    command = rsync_argv('-a', '-M--fake-super', '-M--chmod-dest=Fg+w',
                         f'{context.source}/', f'{context.destination}/')
    result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=20)
    if result.returncode != 23 or not marker.exists() or 'cannot set backup mode' not in result.stderr:
        test_fail(f'expected a reported mode failure: {result.stderr!r}')
    if (previous.read_bytes() != b'previous backup\n' or previous.stat().st_ino != inode
            or stat.S_IMODE(previous.stat().st_mode) != 0o600
            or list(context.destination.iterdir()) != [previous]):
        test_fail('failed mode update changed the previous backup or left a temporary file')

    result = subprocess.run(command, capture_output=True, text=True, timeout=20)
    if (result.returncode != 0 or previous.read_bytes() != b'payload data\n'
            or stat.S_IMODE(previous.stat().st_mode) != 0o664):
        test_fail(f'control did not install the backup: {result.stderr!r}')


if __name__ == '__main__':
    run(test)

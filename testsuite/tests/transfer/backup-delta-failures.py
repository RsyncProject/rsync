#!/usr/bin/env python3

import os
import subprocess

from harness import TestContext, requires, run
from harness.results import test_fail, test_skipped
from harness.rsync import rsync_argv, under_valgrind
from harness.xattrs import is_supported as xattrs_supported


@requires(features={'linux_interpose'}, transports={'pipe'},
          mutates={'filesystem', 'process'}, tags={'transfer', 'regression'})
def test(context: TestContext):
    if under_valgrind():
        test_skipped('requires direct write interposition', capability='linux_interpose')

    fixture = context.repository / 'testsuite/fixtures/backup-delta-failures.c'
    library = context.scratch / 'backup-delta-failures.so'
    build = subprocess.run(['cc', '-shared', '-fPIC', '-o', str(library), str(fixture), '-ldl'],
                           capture_output=True, text=True, timeout=30)
    if build.returncode:
        test_fail(f'cannot build backup write hook: {build.stderr}')

    old = b'a' * 4096 + b'b' * 4096
    new = b'a' * 4096 + b'c' * 4097
    for failure in ('write', 'xattr', 'close'):
        if failure == 'xattr' and not xattrs_supported():
            continue
        base = context.scratch / failure
        source = base / 'source' / 'outer'
        destination = base / 'destination' / 'outer'
        backup = base / 'backup'
        source.mkdir(parents=True)
        destination.mkdir(parents=True)
        backup.mkdir()
        (source / 'file').write_bytes(new)
        (destination / 'file').write_bytes(old)
        if failure == 'xattr':
            os.setxattr(source / 'file', 'user.backup_error', b'value')
            os.setxattr(destination / 'file', 'user.backup_error', b'value')
        saved = backup / 'outer' / 'file'
        marker = base / 'failure-triggered'
        env = {**os.environ, 'LD_PRELOAD': str(library),
               'RSYNC_TEST_BACKUP_FILE': str(saved),
               'RSYNC_TEST_BACKUP_MARKER': str(marker),
               'RSYNC_TEST_BACKUP_FAILURE': failure}
        args = ['-a', '--inplace', '--no-whole-file', f'--backup-dir={backup}']
        if failure == 'xattr':
            args.append('-X')

        result = subprocess.run(
            rsync_argv(*args, f'{base / "source"}/', f'{base / "destination"}/'),
            env=env, capture_output=True, text=True, timeout=20,
        )
        if not marker.exists():
            test_fail(f'{failure}: the backup failure hook did not run')
        if result.returncode == 0:
            test_fail(f'{failure}: failed backup was reported as a successful transfer')
        if (destination / 'file').read_bytes() != old:
            test_fail(f'{failure}: destination changed after its backup failed')


if __name__ == '__main__':
    run(test)

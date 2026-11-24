#!/usr/bin/env python3

import os
import subprocess

from harness import TestContext, requires, run
from harness.results import test_fail, test_skipped
from harness.rsync import rsync_argv, under_valgrind


@requires(features={'linux_interpose'}, transports={'pipe'},
          mutates={'filesystem', 'process'}, tags={'transfer', 'regression'})
def test(context: TestContext):
    if under_valgrind():
        test_skipped('requires direct read interposition', capability='linux_interpose')

    fixture = context.repository / 'testsuite/fixtures/backup-delta-size-change.c'
    library = context.scratch / 'backup-delta-size-change.so'
    build = subprocess.run(['cc', '-shared', '-fPIC', '-o', str(library), str(fixture), '-ldl'],
                           capture_output=True, text=True, timeout=30)
    if build.returncode:
        test_fail(f'cannot build size-change hook: {build.stderr}')

    old = b'a' * 16384
    new = b'b' * 16385
    for mutation in ('grow', 'shrink'):
        base = context.scratch / mutation
        source = base / 'source' / 'outer'
        destination = base / 'destination' / 'outer'
        backup = base / 'backup'
        source.mkdir(parents=True)
        destination.mkdir(parents=True)
        backup.mkdir()
        (source / 'file').write_bytes(new)
        original = destination / 'file'
        original.write_bytes(old)
        marker = base / 'changed'
        env = {**os.environ, 'LD_PRELOAD': str(library),
               'RSYNC_TEST_OLD_FILE': str(original),
               'RSYNC_TEST_CHANGE_MARKER': str(marker),
               'RSYNC_TEST_BACKUP_MUTATION': mutation}
        result = subprocess.run(
            rsync_argv('-a', '--inplace', '--no-whole-file', f'--backup-dir={backup}',
                       f'{base / "source"}/', f'{base / "destination"}/'),
            env=env, capture_output=True, text=True, timeout=20,
        )
        if not marker.exists():
            test_fail(f'{mutation}: the old destination did not change during backup')
        if mutation == 'grow':
            if result.returncode or (backup / 'outer' / 'file').read_bytes() != old:
                test_fail(f'growth was not capped at the captured size: {result.stderr}')
            if original.read_bytes() != new:
                test_fail('the updated file has incorrect contents after growth')
        elif result.returncode == 0 or original.read_bytes() != old[:8192]:
            test_fail(f'short input allowed the in-place update: {result.stderr}')


if __name__ == '__main__':
    run(test)

#!/usr/bin/env python3

import os
import stat
import subprocess

from harness import TestContext, requires, run
from harness.results import test_fail, test_skipped
from harness.rsync import rsync_argv, under_valgrind


@requires(features={'linux_interpose', 'nonroot'}, transports={'pipe'},
          mutates={'filesystem', 'process'}, tags={'transfer', 'regression'})
def test(context: TestContext):
    if under_valgrind():
        test_skipped('requires direct fchmod interposition', capability='linux_interpose')

    fixture = context.repository / 'testsuite/fixtures/backup-mode-interrupt.c'
    library = context.scratch / 'backup-mode-interrupt.so'
    build = subprocess.run(['cc', '-shared', '-fPIC', '-o', str(library), str(fixture), '-ldl'],
                           capture_output=True, text=True, timeout=30)
    if build.returncode:
        test_fail(f'cannot build permission interruption hook: {build.stderr}')

    source = context.source / 'outer'
    destination = context.destination / 'outer'
    backup = context.scratch / 'backup'
    saved = backup / 'outer'
    for path in (source, destination, saved):
        path.mkdir(parents=True)
    (source / 'file').write_bytes(b'replacement data')
    (destination / 'file').write_bytes(b'original')
    saved.chmod(0o555)
    env = {**os.environ, 'LD_PRELOAD': str(library), 'RSYNC_TEST_BACKUP_DIR': str(saved)}
    try:
        result = subprocess.run(
            rsync_argv('-a', '--inplace', '--whole-file', f'--backup-dir={backup}',
                       f'{context.source}/', f'{context.destination}/'),
            env=env, capture_output=True, text=True, timeout=20,
        )
        if result.returncode == 0 or 'test: backup directory made writable' not in result.stderr:
            test_fail(f'rsync did not take the interrupted permission path:\n{result.stderr}')
        if stat.S_IMODE(saved.stat().st_mode) != 0o555:
            test_fail('handled shutdown left the backup directory writable')
        if (destination / 'file').read_bytes() != b'original':
            test_fail('the receiver updated the original before backing it up')
    finally:
        saved.chmod(0o700)


if __name__ == '__main__':
    run(test)

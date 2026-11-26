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
    if os.geteuid() == 0:
        test_skipped('requires non-root directory permissions', capability='nonroot')
    if under_valgrind():
        test_skipped('requires direct tmpfile interposition', capability='linux_interpose')

    fixture = context.repository / 'testsuite/fixtures/backup-lock-unavailable.c'
    library = context.scratch / 'backup-lock-unavailable.so'
    build = subprocess.run(['cc', '-shared', '-fPIC', '-o', str(library), str(fixture)],
                           capture_output=True, text=True, timeout=30)
    if build.returncode:
        test_fail(f'cannot build lock failure hook: {build.stderr}')

    source = context.source
    destination = context.destination
    backup = context.scratch / 'backup'
    original = destination / 'outer' / 'old'
    saved = backup / 'outer'
    source.mkdir()
    original.parent.mkdir(parents=True)
    saved.mkdir(parents=True)
    original.write_bytes(b'previous contents')
    saved.chmod(0o555)
    env = {**os.environ, 'LD_PRELOAD': str(library)}
    try:
        result = subprocess.run(
            rsync_argv('-a', '--delete-during', f'--backup-dir={backup}',
                       f'{source}/', f'{destination}/'),
            env=env, capture_output=True, text=True, timeout=20,
        )
        if result.returncode != 23 or original.read_bytes() != b'previous contents':
            test_fail(f'failed backup changed the original: {result.returncode} {result.stderr}')
        if stat.S_IMODE(saved.stat().st_mode) != 0o555:
            test_fail('failed file backup changed the saved parent directory mode')
        if (saved / 'old').exists():
            test_fail('failed backup published a file')
    finally:
        saved.chmod(0o700)


if __name__ == '__main__':
    run(test)

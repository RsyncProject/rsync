#!/usr/bin/env python3

import os
import subprocess

from harness import TestContext, requires, run
from harness.results import test_fail
from harness.rsync import rsync_argv


@requires(features={'linux_interpose'}, transports={'pipe'}, min_peer='4.0.0',
          mutates={'filesystem', 'process'}, tags={'transfer', 'regression'})
def test(context: TestContext):
    fixture = context.repository / 'testsuite/fixtures/file-budget-redo.c'
    library = context.scratch / 'file-budget-redo.so'
    build = subprocess.run(['cc', '-shared', '-fPIC', '-o', str(library), str(fixture), '-ldl'],
                           capture_output=True, text=True, timeout=30)
    if build.returncode:
        test_fail(f'cannot build the basis-read fixture: {build.stderr}')
    context.source.mkdir()
    context.destination.mkdir()
    data = b'a' * 65536 + b'b' * 65536
    (context.source / 'a-file').write_bytes(data)
    (context.source / 'z-later').write_bytes(b'1')
    basis = context.destination / 'a-file'
    basis.write_bytes(b'a' * 65536 + b'c' * 65536)
    marker = context.scratch / 'first-basis-read'
    env = {**os.environ, 'LD_PRELOAD': str(library), 'LC_ALL': 'C',
           'RSYNC_TEST_BUDGET_BASIS': str(basis.resolve()),
           'RSYNC_TEST_BUDGET_READER': str(marker)}
    result = subprocess.run(
        rsync_argv('-rvI', '--no-whole-file', f'--file-transfer-budget={len(data)}',
                   f'{context.source}/', f'{context.destination}/'),
        env=env, capture_output=True, text=True, timeout=30)
    output = result.stdout + result.stderr
    if result.returncode != 23 or 'z-later' not in output or 'will try again' not in output:
        test_fail(f'the expected budget refusal and checksum retry did not run: {output}')
    if basis.read_bytes() != data or (context.destination / 'z-later').exists():
        test_fail('the admitted retry was withheld or the later file was installed')


if __name__ == '__main__':
    run(test)

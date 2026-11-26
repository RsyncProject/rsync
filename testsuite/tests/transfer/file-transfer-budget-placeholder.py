#!/usr/bin/env python3

import os
import stat
import subprocess
import sys

from harness import TestContext, requires, run
from harness.results import test_fail, test_skipped
from harness.rsync import forced_protocol, rsync_argv


@requires(features={'linux_interpose', 'protocol_30'}, protocols={30, 31, 32, 33}, transports={'pipe'},
          min_peer='4.0.0', mutates={'filesystem', 'process'}, tags={'transfer', 'regression'})
def test(context: TestContext):
    if sys.platform != 'linux':
        test_skipped('the placeholder hook uses Linux interposition', capability='linux_interpose')
    if (forced_protocol() or 30) < 30:
        test_skipped('incremental placeholders require protocol 30 or later', capability='protocol_30')
    fixture = context.repository / 'testsuite/fixtures/file-budget-placeholder.c'
    library = context.scratch / 'file-budget-placeholder.so'
    build = subprocess.run(['cc', '-shared', '-fPIC', '-o', str(library), str(fixture), '-ldl'],
                           capture_output=True, text=True, timeout=30)
    if build.returncode:
        test_fail(f'cannot build the placeholder fixture: {build.stderr}')

    for replacement in ('missing', 'file'):
        source = context.scratch / ('from-' + replacement)
        dest = context.scratch / ('to-' + replacement)
        source.mkdir()
        dest.mkdir()
        for relative in ('a-stop', 'z-created'):
            (source / relative).mkdir()
        (dest / 'a-stop').mkdir()
        (source / 'a-stop/refused').write_bytes(b'x')
        (source / 'z-created/unscheduled').write_bytes(b'x')
        os.chmod(source / 'z-created', 0o750)
        os.utime(source / 'z-created', (1600000000, 1600000000))
        placeholder = dest / 'z-created'
        marker = context.scratch / ('changed-' + replacement)
        env = {**os.environ, 'LD_PRELOAD': str(library), 'LC_ALL': 'C',
               'RSYNC_TEST_BUDGET_PLACEHOLDER': str(placeholder.resolve()),
               'RSYNC_TEST_BUDGET_CHANGED': str(marker),
               'RSYNC_TEST_BUDGET_REPLACE': replacement}
        result = subprocess.run(
            rsync_argv('-rpt', '--inc-recursive', '--file-transfer-budget=0',
                       f'{source}/', f'{dest}/'),
            env=env, capture_output=True, text=True, timeout=30)
        if result.returncode != 23 or 'a-stop/refused' not in result.stderr or not marker.is_file():
            test_fail(f'{replacement}: placeholder change or refusal did not run: {result.stderr}')
        if (dest / 'a-stop/refused').exists():
            test_fail(f'{replacement}: refused contents were installed')
        if replacement == 'missing':
            if placeholder.exists():
                test_fail('finalisation recreated the missing placeholder')
        elif not placeholder.is_file() or placeholder.read_bytes() != b'keep':
            test_fail('finalisation replaced the changed placeholder')
        else:
            info = placeholder.stat()
            if stat.S_IMODE(info.st_mode) != 0o600 or info.st_mtime_ns != 1500000000000000000:
                test_fail('finalisation changed the replacement file metadata')


if __name__ == '__main__':
    run(test)

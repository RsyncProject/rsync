#!/usr/bin/env python3

import os
import subprocess

from harness import TestContext, requires, run
from harness.results import test_fail
from harness.rsync import rsync_argv


@requires(features={'linux_interpose'}, transports={'pipe'}, min_peer='4.0.0',
          mutates={'filesystem', 'process'}, tags={'regression'})
def test(context: TestContext):
    fixture = context.repository / 'testsuite/fixtures/log-open-enoent.c'
    library = context.scratch / 'log-open-enoent.so'
    build = subprocess.run(['cc', '-shared', '-fPIC', '-o', str(library), str(fixture), '-ldl'],
                           capture_output=True, text=True, timeout=30)
    if build.returncode:
        test_fail(f'cannot build logfile creation hook: {build.stderr}')

    source = context.scratch / 'sample'
    source.write_bytes(b'ordinary file data\n')
    for scenario in ('created', 'missing'):
        directory = context.scratch / scenario
        directory.mkdir()
        logfile = directory / 'raced.log'
        destination = directory / 'copy'
        env = {**os.environ, 'LD_PRELOAD': str(library), 'LC_ALL': 'C',
               'RSYNC_TEST_LOG_CREATE': scenario}
        result = subprocess.run(rsync_argv(f'--log-file={logfile}', '--log-file-format=entry:%n',
                                          str(source), str(destination)),
                                env=env, capture_output=True, text=True, timeout=15)
        if result.returncode or destination.read_bytes() != source.read_bytes():
            test_fail(f'{scenario}: transfer failed: {result.returncode}\n{result.stdout}{result.stderr}')
        if scenario == 'created':
            records = logfile.read_bytes()
            if (not records.startswith(b'created by another writer\n')
                    or records.count(b'] entry:sample\n') != 1 or 'failed to open log-file' in result.stderr):
                test_fail(f'concurrent creation lost the logfile record: {records!r}\n{result.stderr}')
        elif logfile.exists() or 'failed to open log-file' not in result.stderr:
            test_fail(f'a still-missing logfile was not reported: {result.stdout}{result.stderr}')


if __name__ == '__main__':
    run(test)

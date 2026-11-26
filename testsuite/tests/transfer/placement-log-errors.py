#!/usr/bin/env python3

import os
import subprocess

from harness import TestContext, requires, run
from harness.results import test_fail
from harness.rsync import forced_protocol, rsync_argv


@requires(features={'linux_interpose'}, transports={'pipe'}, min_peer='4.0.0',
          mutates={'filesystem', 'process'}, tags={'transfer', 'regression'})
def test(context: TestContext):
    fixture = context.repository / 'testsuite/fixtures/placement-error.c'
    library = context.scratch / 'placement-error.so'
    build = subprocess.run(['cc', '-shared', '-fPIC', '-o', str(library), str(fixture), '-ldl'],
                           capture_output=True, text=True, timeout=30)
    if build.returncode:
        test_fail(f'cannot build placement fixture: {build.stderr}')

    context.source.mkdir()
    (context.source / 'blocked').write_bytes(b'new blocked data\n')
    (context.source / 'placed').write_bytes(b'verified payload\n')
    env = {**os.environ, 'LD_PRELOAD': str(library), 'LC_ALL': 'C'}

    for label, options in (('normal', ()), ('delayed', ('--delay-updates',))):
        dest = context.scratch / label
        dest.mkdir()
        previous = dest / 'blocked'
        previous.write_bytes(b'previous content\n')
        event = context.scratch / f'{label}.placements'
        result = subprocess.run(
            rsync_argv('-rI', *options, f'--placement-log={event}', f'{context.source}/', f'{dest}/'),
            env={**env, 'RSYNC_TEST_PLACEMENT_NAME': 'blocked'},
            capture_output=True, text=True, timeout=30)
        if result.returncode != 23 or 'rename' not in result.stderr:
            test_fail(f'{label}: the rename failure was not reported: {result.stderr}')
        if (previous.read_bytes() != b'previous content\n'
                or (dest / 'placed').read_bytes() != b'verified payload\n'
                or event.read_bytes() != b'placed\0'):
            test_fail(f'{label}: the log did not distinguish failed and successful placement')

    context.destination.mkdir()
    event = context.scratch / 'full.placements'
    result = subprocess.run(
        rsync_argv('-r', f'--placement-log={event}', str(context.source / 'placed'),
                   f'{context.destination}/'),
        env={**env, 'RSYNC_TEST_PLACEMENT_LOG': str(event.resolve())},
        capture_output=True, text=True, timeout=30)
    if result.returncode != 23 or 'cannot write placement log' not in result.stderr:
        test_fail(f'log delivery failure was not reported: {result.stderr}')
    if ((context.destination / 'placed').read_bytes() != b'verified payload\n'
            or event.read_bytes()):
        test_fail('a log failure changed the installed file or reported a placement')

    occupied = context.scratch / 'occupied'
    unused = context.scratch / 'unused'
    occupied.symlink_to(unused)
    result = subprocess.run(
        rsync_argv('-r', f'--placement-log={occupied}', str(context.source / 'placed'),
                   f'{context.destination}/'),
        capture_output=True, text=True, timeout=30)
    open_failures = (11, 12) if (forced_protocol() or 31) < 31 else (11,)
    if (result.returncode not in open_failures or 'cannot create placement log' not in result.stderr
            or not occupied.is_symlink() or unused.exists()):
        test_fail('an exclusive log creation followed a dangling symlink')

    raw = context.source / 'row\nbreak'
    raw.write_bytes(b'verified payload\n')
    event = context.scratch / 'raw.placements'
    result = subprocess.run(
        rsync_argv('-r', f'--placement-log={event}', str(raw), f'{context.destination}/'),
        capture_output=True, text=True, timeout=30)
    if result.returncode or event.read_bytes() != b'row\nbreak\0':
        test_fail(f'the log did not preserve the pathname bytes: {result.stderr}')


if __name__ == '__main__':
    run(test)

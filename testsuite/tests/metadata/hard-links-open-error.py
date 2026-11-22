#!/usr/bin/env python3

import os
import signal
import subprocess

from harness import metadata
from harness.environment import SCRATCHDIR
from harness.filesystem import rmtree
from harness.results import test_fail, test_skipped
from harness.rsync import rsync_argv


metadata(features={'dac_restriction', 'nonroot'}, transports={'pipe'},
         mutates={'filesystem', 'process'})

if os.geteuid() == 0:
    test_skipped('root can open a mode-000 source file', capability='nonroot')

base = SCRATCHDIR / 'hard-links-open-error'
rmtree(base)
source = base / 'source'
source.mkdir(parents=True)
(source / 'c1').write_text('new content\n')
os.link(source / 'c1', source / 'c2')
os.link(source / 'c1', source / 'c3')
os.chmod(source / 'c1', 0)
try:
    open(source / 'c1', 'rb').close()
except PermissionError:
    pass
else:
    test_skipped('mode 000 does not prevent this user reading the source',
                 capability='dac_restriction')


def transfer(name, *options):
    destination = base / name
    destination.mkdir()
    (destination / 'c1').write_text('older content\n')
    os.link(destination / 'c1', destination / 'c2')
    os.link(destination / 'c1', destination / 'c3')
    os.utime(destination / 'c1', (1577836800, 1577836800))
    with subprocess.Popen(
            rsync_argv('-aH', *options, f'{source}/', f'{destination}/'),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            start_new_session=True) as process:
        try:
            output, _ = process.communicate(timeout=8)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            output, _ = process.communicate(timeout=5)
            test_fail(f'{name}: transfer stalled after the source open failed: '
                      f'{output!r}')
    if process.returncode != 23:
        test_fail(
            f'{name}: expected exit 23, got {process.returncode}: {output!r}')
    if 'Permission denied' not in output:
        test_fail(f'{name}: source open failure was not reported: {output!r}')
    first = destination / 'c1'
    second = destination / 'c2'
    third = destination / 'c3'
    if any(path.read_text() != 'older content\n'
           for path in (first, second, third)):
        test_fail(f'{name}: failed transfer changed destination content')
    if len({path.stat().st_ino for path in (first, second, third)}) != 1:
        test_fail(f'{name}: failed transfer split the existing hard-link group')


transfer('incremental')
transfer('non-incremental', '--no-inc-recursive')

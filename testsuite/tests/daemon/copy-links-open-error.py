#!/usr/bin/env python3

import errno
import os
import subprocess

from harness import TestContext, requires, run
from harness.daemon import start_test_daemon
from harness.environment import FROMDIR, SCRATCHDIR
from harness.results import test_fail
from harness.rsync import forced_protocol, rsync_argv


@requires(features={'linux_interpose'}, transports={'pipe'}, mutates={'filesystem', 'process', 'socket'}, tags={'daemon', 'io'})
def test(context: TestContext):
    library = SCRATCHDIR / 'sender-open-error.so'
    fixture = context.repository / 'testsuite/fixtures/sender-open-error.c'
    build = subprocess.run(['cc', '-shared', '-fPIC', '-o', str(library), str(fixture), '-ldl'], capture_output=True, text=True)
    if build.returncode:
        test_fail(f'cannot build open hook: {build.stderr}')
    FROMDIR.mkdir()
    (FROMDIR / 'file').write_text('file data\n')
    config = SCRATCHDIR / 'rsyncd.conf'
    identity = f'uid = {os.getuid()}\ngid = {os.getgid()}\n' if os.geteuid() == 0 else ''
    config.write_text(f'use chroot = no\n[files]\npath = {FROMDIR}\n' + identity)
    url = start_test_daemon(config, 12919)
    marker = SCRATCHDIR / 'open-error'
    for error, expected in ((0, 0), (errno.ENOENT, 24), (errno.EACCES, 23)):
        if error == errno.ENOENT and (forced_protocol() or 33) < 30:
            expected = 0
        marker.unlink(missing_ok=True)
        destination = SCRATCHDIR / f'destination-{error}'
        env = {**os.environ, 'LD_PRELOAD': str(library), 'LC_ALL': 'C', 'RSYNC_TEST_OPEN_FILE': 'file',
               'RSYNC_TEST_OPEN_ERRNO': str(error), 'RSYNC_TEST_OPEN_MARKER': str(marker)}
        result = subprocess.run(rsync_argv('-rL', f'{url}files/', f'{destination}/'), env=env,
                                capture_output=True, text=True, timeout=15)
        if result.returncode != expected:
            test_fail(f'open errno {error}: expected exit {expected}, got {result.returncode}: {result.stderr}')
        if error and not marker.exists():
            test_fail('open hook did not fire')
        if error and (destination / 'file').exists():
            test_fail('failed open produced a destination file')
        if error == errno.EACCES and os.strerror(error) not in result.stderr:
            test_fail(f'open error was overwritten: {result.stderr}')
        if not error and (destination / 'file').read_text() != 'file data\n':
            test_fail('open control changed file data')


if __name__ == '__main__':
    run(test)

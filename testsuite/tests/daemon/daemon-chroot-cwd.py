#!/usr/bin/env python3

import ctypes
import os
import subprocess

from harness.filesystem import unlink_if_exists
from harness import TestContext, requires, run
from harness.daemon import start_test_daemon
from harness.environment import SCRATCHDIR
from harness.results import test_fail, test_skipped
from harness.rsync import rsync_argv


@requires(features={'chroot', 'linux_interpose', 'root'}, transports={'pipe'}, mutates={'filesystem', 'process', 'socket'}, tags={'daemon'})
def test(context: TestContext):
    pid = os.fork()
    if pid == 0:
        os._exit(0 if ctypes.CDLL(None, use_errno=True).chroot(b'/') == 0 else 1)
    _, status = os.waitpid(pid, 0)
    if not os.WIFEXITED(status) or os.WEXITSTATUS(status):
        test_skipped('chroot is unavailable', capability='chroot')
    library = SCRATCHDIR / 'chroot-cwd.so'
    fixture = context.repository / 'testsuite/fixtures/chroot-cwd.c'
    build = subprocess.run(['cc', '-shared', '-fPIC', '-o', str(library), str(fixture), '-ldl'], capture_output=True, text=True)
    if build.returncode:
        test_fail(f'cannot build chroot hook: {build.stderr}')
    root = SCRATCHDIR / 'root'
    inner = root / 'inner'
    inner.mkdir(parents=True)
    (root / 'outside-inner').write_text('outside\n')
    (inner / 'inside').write_text('inside\n')
    config = SCRATCHDIR / 'rsyncd.conf'
    marker = SCRATCHDIR / 'chdir'
    url = start_test_daemon(config, 12920)
    for name, path, denied in (('plain', str(root), False), ('inner', f'{root}/./inner', False),
                               ('denied', f'{root}/./inner', True)):
        unlink_if_exists(marker)
        config.write_text(f'use chroot = yes\n[files]\npath = {path}\n')
        env = {**os.environ, 'LD_PRELOAD': str(library), 'RSYNC_TEST_CHROOT_PATH': str(root),
               'RSYNC_TEST_CHROOT_MARKER': str(marker)}
        if denied:
            env['RSYNC_TEST_CHROOT_DENY'] = '1'
        destination = SCRATCHDIR / name
        result = subprocess.run(rsync_argv('-r', f'{url}files/', f'{destination}/'), env=env,
                                capture_output=True, text=True, timeout=15)
        if not marker.exists() or marker.read_text() != '/\n':
            test_fail(f'{name}: did not enter the new root immediately')
        if (result.returncode != 0) != denied:
            test_fail(f'{name}: unexpected exit {result.returncode}: {result.stderr}')
        if name == 'plain' and (destination / 'outside-inner').read_text() != 'outside\n':
            test_fail('plain chroot selected the wrong directory')
        if name == 'inner' and ((destination / 'inside').read_text() != 'inside\n'
                                or (destination / 'outside-inner').exists()):
            test_fail('inner chroot selected the wrong directory')


if __name__ == '__main__':
    run(test)

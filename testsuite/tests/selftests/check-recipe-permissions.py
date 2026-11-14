#!/usr/bin/env python3

import os
import subprocess
import sys

from harness import TestContext, requires, run
from harness.results import test_fail, test_skipped
from harness.rsync import rsync_command_binary

@requires(features={'nonroot', 'dac_restriction'}, transports={'pipe'}, mutates={'filesystem', 'process'}, tags={'harness'})
def test(context: TestContext):
    if os.geteuid() == 0:
        test_skipped('root bypasses directory permissions', capability='nonroot')
    blocked = context.scratch / 'read-only'
    blocked.mkdir(mode=0o500)
    try:
        probe = blocked / 'probe'
        try:
            probe.mkdir()
        except PermissionError:
            pass
        else:
            probe.rmdir()
            test_skipped('directory writes are not restricted', capability='dac_restriction')
        recipe = context.repository / 'testsuite' / 'tools' / 'check.py'
        command = [sys.executable, '-S', str(recipe), str(context.tools), rsync_command_binary(), 'pipe,self-peer']
        env = {**os.environ, 'LC_ALL': 'C'}
        control = subprocess.run(command + [str(context.scratch / 'control'), 'cli-transfers'],
                                 env=env, capture_output=True, text=True, timeout=60)
        if control.returncode:
            test_fail(f'permission control failed: {control.stdout}{control.stderr}')
        result = subprocess.run(command + [str(blocked / 'result'), 'cli-transfers'],
                                env=env, capture_output=True, text=True, timeout=60)
        if not result.returncode or 'Permission denied' not in result.stderr:
            test_fail(f'write permission failure was not reported: {result.stdout}{result.stderr}')
    finally:
        blocked.chmod(0o700)

if __name__ == '__main__':
    run(test)

#!/usr/bin/env python3

import shutil
from pathlib import Path
from unittest.mock import patch

from harness import TestContext, requires, run
from harness.interoperability import PLATFORMS, MTIME, endpoint_identity, seed, snapshot, transfer_cases, update
from harness.results import test_fail
from harness.rsync import rsync_command_binary


@requires(transports={'pipe'}, mutates={'filesystem'}, tags={'harness'})
def test(context: TestContext):
    cases = transfer_cases()
    if len(cases) != 168 or len(set(cases)) != 168:
        test_fail('directed transfer cases are incomplete')
    nonlinux = {(source, destination) for source, destination, _, _ in cases if 'linux' not in (source, destination)}
    if len(nonlinux) != 30:
        test_fail('non-Linux transfer pairs are incomplete')
    binary = Path(rsync_command_binary())
    actual = endpoint_identity(binary)['platform']
    other = next(name for name in PLATFORMS if name != actual)
    try:
        endpoint_identity(binary, other)
    except ValueError:
        pass
    else:
        test_fail('host operating system substituted for an endpoint')
    for name, system in PLATFORMS.items():
        with patch('platform.system', return_value='CYGWIN_NT-10.0' if name == 'cygwin' else system):
            if endpoint_identity(binary, name)['platform'] != name:
                test_fail(f'{name}: endpoint identity differs')
    seed(context.source)
    before = snapshot(context.source)
    if (before['entries']['hardlink']['mode'] != 0o640 or before['entries']['empty']['mode'] != 0o750
            or before['entries']['relative-link'] != dict(type='symlink', target='nested/payload')
            or before['entries']['dangling-link'] != dict(type='symlink', target='absent')):
        test_fail('fixture metadata is incomplete')
    update(context.source)
    after = snapshot(context.source)
    first, second = before['entries']['nested/payload'], after['entries']['nested/payload']
    if first['sha256'] == second['sha256'] or first['size'] != second['size'] or first['mtime'] != second['mtime'] or second['mtime'] != MTIME:
        test_fail('checksum fixture does not preserve its quick-check attributes')
    if after['hardlinks'] != [['hardlink', 'nested/payload']] or 'obsolete' in after['entries']:
        test_fail('update fixture lost its links or deletion')
    shutil.copytree(context.source, context.destination, symlinks=True)
    if snapshot(context.destination) == after:
        test_fail('fixture comparison missed split hard links')


if __name__ == '__main__':
    run(test)

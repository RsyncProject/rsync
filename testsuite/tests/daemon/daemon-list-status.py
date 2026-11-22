#!/usr/bin/env python3

import subprocess

from harness import metadata
from harness.daemon_config import write_daemon_conf
from harness.environment import SCRATCHDIR
from harness.filesystem import makepath
from harness.results import test_fail
from harness.rsync import rsync_argv

metadata(transports={'pipe'}, min_peer={'primary': '4.0.0', 'secondary': '2.6.0'},
         mutates={'filesystem', 'process'}, tags={'daemon', 'regression'})

module = SCRATCHDIR / 'module'
makepath(module)
conf = write_daemon_conf([('module', {'path': str(module), 'read only': 'yes'})])
args = rsync_argv('--server', '--daemon', f'--config={conf}', '.')


def request(name):
    return subprocess.run(args, input=f'@RSYNCD: 31.0\n{name}\n',
                          capture_output=True, text=True, timeout=15)


for name in ('#list', ''):
    result = request(name)
    if result.returncode != 0:
        test_fail(f'module list {name!r}: expected exit 0, got {result.returncode}')
    lines = result.stdout.splitlines()
    if not lines or not lines[0].startswith('@RSYNCD: ') or lines[-1] != '@RSYNCD: EXIT':
        test_fail(f'incomplete module list: {result.stdout!r}')
    if not any(line.split()[:1] == ['module'] for line in lines[1:-1]):
        test_fail(f'module missing from listing: {result.stdout!r}')

result = request('missing-module')
if result.returncode == 0 or '@ERROR: Unknown module' not in result.stdout:
    test_fail(f'unknown module did not fail: {result.returncode}: {result.stdout!r}')

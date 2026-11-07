#!/usr/bin/env python3

from harness.daemon_config import write_daemon_conf
from harness.rsync import (
    SCRATCHDIR, makepath, rmtree, run_rsync, start_test_daemon, test_fail, test_skipped,
)

if ' ' in str(SCRATCHDIR):
    test_skipped("rsyncd.conf exclude cannot represent a path containing spaces")

protected = 'protected\n'
base = SCRATCHDIR / 'operator-traversal'
rmtree(base)
base.mkdir()
modules = []
cases = []

for name in ('destination', 'backup', 'partial'):
    root = base / name
    secret = root / 'secret'
    source = root / 'work' / 'src'
    destination = root / 'work' / 'destination'
    makepath(secret, source / 'sub', destination)
    victim = secret / 'f0'
    victim.write_text(protected)
    (source / 'f0').write_text('new\n')
    if name == 'backup':
        (destination / 'f0').write_text('old\n')
    modules.append((name, {'path': '/', 'read only': 'no', 'exclude': f'{secret}/'}))
    cases.append((name, root, source, destination, victim))

url = start_test_daemon(write_daemon_conf(modules, name='operator-traversal.conf'), 12963)

for name, root, source, destination, victim in cases:
    relative = str(root).lstrip('/')
    if name == 'destination':
        args = ('-a', f'{source}/',
                f'{url}{name}/{relative}/work/src/sub/../../../secret/')
    elif name == 'backup':
        args = ('-a', '--backup',
                f'--backup-dir=/{relative}/work/src/sub/../../../secret/',
                f'{source}/', f'{url}{name}/{relative}/work/destination')
    else:
        args = ('-a', f'--partial-dir=/{relative}/work/src/sub/../../../secret/',
                f'{source}/', f'{url}{name}/{relative}/work/destination')
    run_rsync(*args, check=False)
    if not victim.exists() or victim.read_text() != protected:
        test_fail(f'{name}: traversal reached the excluded subtree')

print('operator paths cannot traverse into excluded daemon paths')

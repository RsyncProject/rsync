#!/usr/bin/env python3

from harness.rsync import (SCRATCHDIR, forced_protocol, makepath, rmtree, run_rsync,
                      start_test_daemon, test_fail, write_daemon_conf)

base = SCRATCHDIR / 'operator-path-dir-daemon'
rmtree(base)
modules = []
cases = {}

for name in ('backup', 'partial'):
    root = base / name
    module = root / 'module'
    outside = root / 'outside'
    source = root / 'source'
    makepath(module, outside, source)
    (outside / 'file').write_text('protected\n')
    (module / 'file').write_text('old-destination-content\n')
    (module / 'outside').symlink_to(outside)
    (source / 'file').write_text('new\n')
    modules.append((name, {'path': str(module), 'read only': 'no'}))
    cases[name] = module, outside, source

url = start_test_daemon(write_daemon_conf(modules, name='operator-path-dir.conf'), 12960)

module, outside, source = cases['backup']
run_rsync('-a', '--backup', '--backup-dir=/outside', f'{source}/', f'{url}backup/', check=False)
if (outside / 'file').read_text() != 'protected\n':
    test_fail('backup-dir escaped the daemon module')

module, outside, source = cases['partial']
result = run_rsync('-a', '--delay-updates', '--partial-dir=/outside',
                   f'{source}/', f'{url}partial/', check=False)
destination = module / 'file'
if forced_protocol() is None or forced_protocol() >= 30:
    if result.returncode == 0 or destination.read_text() != 'old-destination-content\n':
        test_fail('partial-dir escape was not rejected')
elif result.returncode or destination.read_text() != 'new\n':
    test_fail('protocol 29 partial-dir transfer failed')
if (outside / 'file').read_text() != 'protected\n':
    test_fail('partial-dir escaped the daemon module')

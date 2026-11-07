#!/usr/bin/env python3

from harness.rsync import (
    SCRATCHDIR, makepath, rmtree, rsync_path_arg, rsh_cmd, run_rsync,
    test_fail,
)
from harness import metadata

metadata(features={'remote-shell'}, transports={'pipe', 'tcp'}, min_peer='2.6.0', mutates={'filesystem', 'process'}, tags={'compatibility', 'transfer', 'version-mix'})

base = SCRATCHDIR / 'old-dirs'
src = base / 'src'
push = base / 'push'
pull = base / 'pull'
rmtree(base)
makepath(src / 'one' / 'two', push, pull)
(src / 'top').write_text('top\n')
(src / 'one' / 'inside').write_text('inside\n')
(src / 'one' / 'two' / 'deep').write_text('deep\n')

ssh = rsh_cmd()
rpath = f'--rsync-path={rsync_path_arg()}'

def assert_shallow(path, label):
    top = path / 'top'
    if not top.is_file() or top.read_text() != 'top\n':
        test_fail(f'{label}: root file was not transferred')
    if not (path / 'one').is_dir():
        test_fail(f'{label}: first-level directory was not transferred')
    if (path / 'one' / 'inside').exists() or (path / 'one' / 'two').exists():
        test_fail(f'{label}: --old-dirs recursed below the first directory level')

run_rsync('--old-dirs', '-e', ssh, rpath,
          f'{src}/', f'localhost:{push}/')
assert_shallow(push, 'current sender')

run_rsync('--old-d', '-e', ssh, rpath,
          f'localhost:{src}/', f'{pull}/')
assert_shallow(pull, 'peer sender')

print('--old-dirs transfers one directory level in both remote directions')

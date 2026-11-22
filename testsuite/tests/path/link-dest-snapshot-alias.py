#!/usr/bin/env python3

import os

from harness import metadata
from harness.environment import SCRATCHDIR
from harness.filesystem import assert_hardlinked, assert_not_hardlinked, assert_same, makepath
from harness.results import test_fail
from harness.rsync import rsh_cmd, rsync_path_arg, run_rsync

metadata(features={'hardlinks', 'remote-shell'}, transports={'pipe'}, mutates={'filesystem'},
         tags={'path', 'regression'})

src = SCRATCHDIR / 'source'
basis = SCRATCHDIR / 'snapshots' / 'previous'
earlier = SCRATCHDIR / 'snapshots' / 'earlier'
latest = SCRATCHDIR / 'latest'
makepath(src / 'sub', earlier / 'sub')
for name in ('exact', 'second', 'sub/exact', 'sub/second', 'sub/changed', 'sub/new'):
    (src / name).write_text(f'{name}\n')

run_rsync('-a', f'{src}/', f'{basis}/')
(basis / 'sub/changed').write_text('old\n')
(basis / 'sub/new').unlink()
(earlier / 'exact').write_text('older version\n')
(earlier / 'sub/exact').mkdir()
os.symlink('snapshots/previous', latest)

for name, alias in (('absolute', latest), ('relative', '../latest')):
    dest = SCRATCHDIR / name
    dest.mkdir()
    argv = ('-aH', f'--link-dest={earlier}', f'--link-dest={alias}', f'{src}/', f'{dest}/')
    run_rsync('-aH', '--dry-run', f'--link-dest={earlier}',
              f'--link-dest={alias}', f'{src}/', f'{dest}/')
    if any(dest.iterdir()):
        test_fail(f'{name}: dry run populated the destination')

    for _ in range(2):
        run_rsync(*argv)
        for entry in ('exact', 'second', 'sub/exact', 'sub/second'):
            assert_hardlinked(basis / entry, dest / entry, label=name)
            assert_same(src / entry, dest / entry, label=name)
        for entry in ('sub/changed', 'sub/new'):
            assert_same(src / entry, dest / entry, label=name)
        assert_not_hardlinked(basis / 'sub/changed', dest / 'sub/changed', label=name)

remote = SCRATCHDIR / 'remote-shell'
run_rsync('-aH', '-e', rsh_cmd(), f'--rsync-path={rsync_path_arg()}',
          f'--link-dest={latest}', f'{src}/', f'localhost:{remote}/')
for entry in ('exact', 'second', 'sub/exact', 'sub/second'):
    assert_hardlinked(basis / entry, remote / entry, label='remote-shell')
for entry in ('sub/changed', 'sub/new'):
    assert_same(src / entry, remote / entry, label='remote-shell')

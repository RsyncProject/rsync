#!/usr/bin/env python3

import os

from harness import metadata
from harness.environment import SCRATCHDIR
from harness.filesystem import assert_hardlinked, assert_mtime_close, assert_same, makepath
from harness.results import test_fail
from harness.rsync import run_rsync

metadata(features={'hardlinks'}, transports={'pipe'}, mutates={'filesystem'},
         tags={'path', 'regression'})

source = SCRATCHDIR / 'source'
basis = SCRATCHDIR / 'basis'
dirs = ('.', 'branch', 'branch/empty', 'branch/filled', 'branch/filled/deep')
entry = 'branch/filled/deep/record'
for name in dirs:
    makepath(source / name)
(source / entry).write_text('unchanged record\n')

stamp = 1_600_000_000
for name in dirs:
    os.utime(source / name, (stamp, stamp))
run_rsync('-a', f'{source}/', f'{basis}/')

for name, options in (('during', ('--delete',)), ('delay', ('--delete-delay',)),
                      ('non-incremental', ('--delete', '--no-inc-recursive')),
                      ('omit-times', ('--delete', '--omit-dir-times'))):
    dest = SCRATCHDIR / name
    run_rsync('-a', *options, f'--link-dest={basis}', f'{source}/', f'{dest}/')
    assert_hardlinked(basis / entry, dest / entry, label=name)
    for directory in dirs:
        path = dest / directory
        if name == 'omit-times':
            if abs(path.stat().st_mtime - stamp) <= 1:
                test_fail(f'--omit-dir-times preserved the time of {directory}')
        else:
            assert_mtime_close(path, stamp, label=f'{name}: {directory}')

dest = SCRATCHDIR / 'replacement'
makepath(dest / 'branch')
(dest / 'branch/filled').write_text('old non-directory entry\n')
run_rsync('-a', '--delete', f'--link-dest={basis}', f'{source}/', f'{dest}/')
assert_same(source / entry, dest / entry, label='directory replaces a file')
assert_hardlinked(basis / entry, dest / entry, label='directory replacement')

dest = SCRATCHDIR / 'existing-only'
run_rsync('-a', '--existing', f'--link-dest={basis}', f'{source}/', f'{dest}/')
if (dest / 'branch').exists():
    test_fail('--existing created a missing directory')

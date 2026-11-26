#!/usr/bin/env python3

from harness import metadata
from harness.environment import SCRATCHDIR
from harness.filesystem import assert_hardlinked, assert_not_hardlinked, assert_same, makepath
from harness.results import test_fail
from harness.rsync import run_rsync

metadata(features={'hardlinks'}, transports={'pipe'}, mutates={'filesystem'},
         tags={'path', 'regression'})

source = SCRATCHDIR / 'source'
basis = SCRATCHDIR / 'snapshots' / 'previous'
latest = SCRATCHDIR / 'latest'
entries = (
    'group/branch/first/one',
    'group/branch/first/two',
    'group/branch/first-more/one',
    'group/branch/linked/one',
    'group/branch/second/one',
    'group/branch/second/two',
)
for name in entries:
    path = source / name
    makepath(path.parent)
    path.write_text(f'{name}\n')

makepath(basis.parent)
run_rsync('-a', f'{source}/', f'{basis}/')
latest.symlink_to('snapshots/previous', target_is_directory=True)

branch = basis / 'group/branch'
(branch / 'linked').rename(branch / 'stored')
(branch / 'linked').symlink_to('stored', target_is_directory=True)
(branch / 'second/two').write_text('different basis contents\n')

for name, options in (('fresh', ()), ('non-incremental', ('--no-inc-recursive',)),
                      ('checksum', ('--checksum',)), ('keep-dirlinks', ('--keep-dirlinks',))):
    dest = SCRATCHDIR / name
    if name == 'keep-dirlinks':
        parent = dest / 'group/branch'
        makepath(parent / 'retained')
        (parent / 'first').symlink_to('retained', target_is_directory=True)

    for _ in range(2):
        run_rsync('-aH', *options, f'--link-dest={latest}', f'{source}/', f'{dest}/')
        for entry in entries:
            assert_same(source / entry, dest / entry, label=name)
            if entry == 'group/branch/second/two':
                assert_not_hardlinked(basis / entry, dest / entry, label=name)
            else:
                assert_hardlinked(basis / entry, dest / entry, label=name)
        if name == 'keep-dirlinks' and not (dest / 'group/branch/first').is_symlink():
            test_fail('--keep-dirlinks replaced the destination directory symlink')

#!/usr/bin/env python3

import os
import stat

from harness import metadata
from harness.environment import SCRATCHDIR
from harness.filesystem import assert_hardlinked, assert_not_hardlinked, assert_same, makepath
from harness.results import test_fail
from harness.rsync import run_rsync

metadata(features={'hardlinks'}, transports={'pipe'}, mutates={'filesystem'},
         tags={'path', 'regression'})

source = SCRATCHDIR / 'source'
basis = SCRATCHDIR / 'basis'
makepath(source / 'branch')
(source / 'branch/a-match').write_text('same\n')
(source / 'branch/c-change').write_text('before\n')
(source / 'branch/d-match').write_text('another match\n')
(source / 'branch/e-mode').write_text('same bytes, different permissions\n')
run_rsync('-a', f'{source}/', f'{basis}/')
(source / 'branch/b-new').write_text('new file\n')
(source / 'branch/c-change').write_text('changed contents\n')
mode_entry = 'branch/e-mode'
basis_mode = stat.S_IMODE((basis / mode_entry).stat().st_mode)
os.chmod(source / mode_entry, 0o600)
source_mode = stat.S_IMODE((source / mode_entry).stat().st_mode)

args = (f'--link-dest={basis}', f'{source}/')
dest = SCRATCHDIR / 'fresh'
run_rsync('-a', '--delete', *args, f'{dest}/')
for name in ('a-match', 'b-new', 'c-change', 'd-match', 'e-mode'):
    path = f'branch/{name}'
    assert_same(source / path, dest / path, label='fresh destination')
    if name.endswith('-match'):
        assert_hardlinked(basis / path, dest / path, label='basis match')
    elif name == 'c-change':
        assert_not_hardlinked(basis / path, dest / path, label='changed file')
if source_mode != basis_mode:
    assert_not_hardlinked(basis / mode_entry, dest / mode_entry, label='changed permissions')
    if stat.S_IMODE((dest / mode_entry).stat().st_mode) != source_mode:
        test_fail('copy did not apply the requested mode')
    if stat.S_IMODE((basis / mode_entry).stat().st_mode) != basis_mode:
        test_fail('copy changed the basis mode')

dest = SCRATCHDIR / 'existing'
makepath(dest / 'branch')
(dest / 'branch/a-match').write_text('pre-existing destination\n')
run_rsync('-a', '--ignore-existing', *args, f'{dest}/')
if (dest / 'branch/a-match').read_text() != 'pre-existing destination\n':
    test_fail('--ignore-existing replaced an existing file with a basis link')

dest = SCRATCHDIR / 'limited'
run_rsync('-a', '--max-size=8', *args, f'{dest}/')
assert_hardlinked(basis / 'branch/a-match', dest / 'branch/a-match', label='under size limit')
if (dest / 'branch/d-match').exists():
    test_fail('--link-dest created a file above the size limit')

dest = SCRATCHDIR / 'dry-run'
run_rsync('-a', '--dry-run', *args, f'{dest}/')
if dest.exists():
    test_fail('--dry-run created a destination directory')

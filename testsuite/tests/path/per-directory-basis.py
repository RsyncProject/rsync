#!/usr/bin/env python3

import os
import re
import subprocess
import sys

from harness import metadata
from harness.environment import SCRATCHDIR
from harness.filesystem import (
    assert_hardlinked, assert_not_exists, assert_not_hardlinked, assert_same, makepath,
)
from harness.results import test_fail, test_skipped
from harness.rsync import rsync_argv, run_rsync

metadata(features={'dirfd_pinning', 'hardlinks', 'symlink'}, transports={'pipe'},
         mutates={'filesystem'}, tags={'path', 'regression'})

if sys.platform == 'cygwin':
    test_skipped('Per-directory basis directories require pinned dirfds', capability='dirfd_pinning')

source = SCRATCHDIR / 'source'
stamp = 1_600_000_000
exact = b'exact contents\n'
old = b'0123456789abcdef' * 8192
changed = b'new\n' * 128 + old[512:]


def put(path, content, mode=0o644, mtime=stamp):
    makepath(path.parent)
    path.write_bytes(content)
    path.chmod(mode)
    os.utime(path, (mtime, mtime))


def transfer(dest, *options):
    return run_rsync('-a', *options, f'{source}/', f'{dest}/', capture_output=True).stdout


put(source / 'show/season/exact', exact)
put(source / 'show/season/mode', exact, 0o600)
put(source / 'show/season/changed', changed, mtime=stamp + 60)
put(source / 'show/season/existing', exact)
put(source / 'show/outer', exact)
put(source / 'top', exact)
put(source / 'show/season/extra', exact)

for mode in ('compare', 'copy'):
    dest = SCRATCHDIR / mode
    put(dest / 'show/exact', exact)
    put(dest / 'show/mode', exact)
    put(dest / 'show/changed', old)
    put(dest / 'show/existing', exact)
    put(dest / 'show/library/extra', exact)
    put(dest / 'outer', exact)
    put(dest / 'library/top', exact)
    put(dest / 'show/library/exact', b'does not match\n', mtime=stamp - 60)
    options = (f'--{mode}-dest-per-dir=library', f'--{mode}-dest-per-dir=../library',
               f'--{mode}-dest-per-dir=..')
    plan = transfer(dest, '-nii', *options)
    assert_not_exists(dest / 'show/season', label='dry run created the target directory')
    item = next((line for line in plan.splitlines() if line.endswith(' show/season/exact')), '')
    if not item.startswith('cf' if mode == 'copy' else '.f'):
        test_fail(f'{mode}: dry run did not use the enclosing directory: {plan!r}')

    # An existing regular destination must use the ordinary transfer.
    put(dest / 'show/season/existing', b'existing differs\n', mtime=stamp - 60)
    output = transfer(dest, '--no-whole-file', '--stats', '--no-human-readable', *options)
    matched = re.search(r'^Matched data: ([0-9,]+) bytes$', output, re.MULTILINE)
    if not matched or int(matched.group(1).replace(',', '')) < len(old) // 2:
        test_fail(f'{mode}: enclosing delta basis was not used: {output!r}')

    for name in ('show/season/changed', 'show/season/mode'):
        assert_same(source / name, dest / name, label=mode)
    if (dest / 'show/season/mode').stat().st_mode & 0o777 != 0o600:
        test_fail(f'{mode}: local copy did not use the source mode')
    assert_not_hardlinked(dest / 'show/mode', dest / 'show/season/mode', label=mode)
    if (dest / 'show/changed').read_bytes() != old:
        test_fail(f'{mode}: delta transfer changed the basis')

    for name, basis in (('show/season/exact', 'show/exact'),
                        ('show/season/extra', 'show/library/extra'),
                        ('show/outer', 'outer'), ('top', 'library/top')):
        if mode == 'compare':
            assert_not_exists(dest / name, label='exact compare match')
        else:
            assert_same(source / name, dest / name, label='exact local copy')
            assert_not_hardlinked(dest / basis, dest / name, label='exact local copy')
    assert_same(source / 'show/season/existing', dest / 'show/season/existing', label=mode)

dest = SCRATCHDIR / 'grandparent'
put(dest / 'exact', exact)
put(SCRATCHDIR / 'top', exact)
transfer(dest, '--compare-dest-per-dir=../..', '--compare-dest-per-dir=..')
assert_not_exists(dest / 'show/season/exact', label='grandparent basis match')
assert_same(source / 'top', dest / 'top', label='entry has no grandparent in the destination')

for mode in ('compare', 'copy', 'link'):
    dest = SCRATCHDIR / f'legacy-{mode}'
    basis = dest / ': hold/show/season/exact'
    target = dest / 'show/season/exact'
    put(basis, exact)
    transfer(dest, f'--{mode}-dest=: hold')
    if mode == 'compare':
        assert_not_exists(target, label='legacy literal compare directory')
    elif mode == 'link':
        assert_hardlinked(basis, target, label='legacy literal link directory')
    else:
        assert_same(basis, target, label='legacy literal copy directory')
        assert_not_hardlinked(basis, target, label='legacy literal copy directory')

one = SCRATCHDIR / 'one'
put(one / 'top', exact)
dest = SCRATCHDIR / 'directory-alias'
put(dest / 'store/top', exact)
(dest / 'library').symlink_to('store', target_is_directory=True)
run_rsync('-a', '--compare-dest-per-dir=library', f'{one}/', f'{dest}/')
assert_not_exists(dest / 'top', label='in-tree directory alias')

dest = SCRATCHDIR / 'external-alias'
makepath(dest)
put(SCRATCHDIR / 'store/top', exact)
(dest / 'library').symlink_to('../store', target_is_directory=True)
run_rsync('-a', '--compare-dest-per-dir=library', f'{one}/', f'{dest}/')
assert_same(one / 'top', dest / 'top', label='candidate directory outside the destination')

private_source = SCRATCHDIR / 'private-source'
dest = SCRATCHDIR / 'private-delta'
put(private_source / 'top', changed, 0o600, stamp + 60)
put(dest / 'library/top', old, 0o666)
run_rsync('-rt', '--no-whole-file', '--copy-dest-per-dir=library',
          f'{private_source}/', f'{dest}/')
assert_same(private_source / 'top', dest / 'top', label='delta without permission preservation')
if (dest / 'top').stat().st_mode & 0o777 != 0o600:
    test_fail('A missing destination inherited permissions from the alternate delta basis')

if os.geteuid() != 0:
    dest = SCRATCHDIR / 'inaccessible-basis'
    put(dest / 'private/top', exact)
    (dest / 'private').chmod(0)
    try:
        result = run_rsync('-a', '--copy-dest-per-dir=private', f'{one}/', f'{dest}/',
                           check=False, capture_output=True)
    finally:
        (dest / 'private').chmod(0o700)
    if result.returncode != 23 or 'cannot inspect per-directory basis' not in result.stderr:
        test_fail(f'Inaccessible basis was not reported: {result.stderr!r}')
    assert_same(one / 'top', dest / 'top', label='ordinary transfer after inaccessible basis')

if sys.platform.startswith('linux'):
    dest = SCRATCHDIR / 'long-basis'
    makepath(dest)
    limit = os.pathconf(dest, 'PC_PATH_MAX')
    path = 'x/' * (limit // 2 - 1)
    result = run_rsync('-a', f'--copy-dest-per-dir={path}', f'{one}/', f'{dest}/',
                       check=False, capture_output=True)
    if result.returncode != 23 or 'cannot inspect per-directory basis' not in result.stderr:
        test_fail(f'Overlong joined basis path was not reported: {result.stderr!r}')
    assert_same(one / 'top', dest / 'top', label='ordinary transfer after overlong basis path')

for options in (
    ('--copy-dest-per-dir=',),
    ('--copy-dest-per-dir=/ordinary',),
    ('--copy-dest-per-dir=ordinary/../other',),
    ('--copy-dest-per-dir=' + 'ordinary/' * 2000,),
    ('--copy-dest-per-dir=..', '--copy-dest=other'),
    ('--copy-dest-per-dir=..', '--compare-dest-per-dir=other'),
    ('--copy-dest-per-dir=..', '--delete-delay'),
    ('--copy-dest-per-dir=..', '--force'),
    ('--copy-dest-per-dir=..', '--fuzzy'),
    ('--copy-dest-per-dir=..', '--hard-links'),
    ('--copy-dest-per-dir=..', '--inplace'),
    ('--copy-dest-per-dir=..', '--partial-dir=partial'),
    ('--copy-dest-per-dir=..', '--protocol=28'),
    ('--link-dest-per-dir=..',),
):
    dest = SCRATCHDIR / 'refused'
    makepath(dest)
    result = run_rsync('-a', *options, f'{source}/', f'{dest}/', check=False, capture_output=True)
    if result.returncode != 1 or 'per-dir' not in result.stderr or any(dest.iterdir()):
        test_fail(f'{options[0][:60]} was not refused before destination changes: {result.stderr!r}')

dest = SCRATCHDIR / 'missing'
result = run_rsync('-a', '--copy-dest-per-dir=..', f'{source}/', f'{dest}/',
                   check=False, capture_output=True)
if result.returncode != 1 or 'existing destination directory' not in result.stderr:
    test_fail(f'Non-existent destination was not refused: {result.stderr!r}')
assert_not_exists(dest, label='refused destination root')

dest = SCRATCHDIR / 'ordinary-file'
put(dest, old)
result = run_rsync('-a', '--copy-dest-per-dir=..', str(one / 'top'), str(dest),
                   check=False, capture_output=True)
if result.returncode != 1 or 'existing destination directory' not in result.stderr:
    test_fail(f'Non-directory destination was not refused: {result.stderr!r}')
if dest.read_bytes() != old:
    test_fail('Non-directory destination changed during refusal')

dest = SCRATCHDIR / 'remote'
result = run_rsync('-a', '-e', 'false', '--copy-dest-per-dir=..',
                   f'{source}/', f'localhost:{dest}/', check=False, capture_output=True)
if result.returncode != 1 or 'local transfer' not in result.stderr:
    test_fail(f'Remote transfer was not refused locally: {result.stderr!r}')
assert_not_exists(dest, label='refused remote destination')

for protected in ((), ('-s',)):
    result = subprocess.run(
        rsync_argv('--copy-dest-per-dir=..', '--server', *protected, '.', str(dest)),
        stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=5,
    )
    if result.returncode != 1 or '--copy-dest-per-dir requires a local transfer' not in result.stderr:
        test_fail(f'Server invocation did not refuse per-directory basis: {result.stderr}')
assert_not_exists(dest, label='refused server destination')

#!/usr/bin/env python3

import os
import re

from harness import metadata
from harness.environment import SCRATCHDIR
from harness.filesystem import (
    assert_hardlinked, assert_not_exists, assert_not_hardlinked, assert_same, makepath,
)
from harness.results import test_fail
from harness.rsync import run_rsync

metadata(features={'hardlinks', 'symlink'}, transports={'pipe'}, mutates={'filesystem'},
         tags={'path', 'regression'})

source = SCRATCHDIR / 'source'
basis = SCRATCHDIR / 'basis'
latest = SCRATCHDIR / 'latest'
stamp = 1_600_000_000
unchanged = 'branch/first/deep/unchanged'
entries = {'branch/first/deep/first': b'abcd' * 32768,
           'branch/second/deep/second': b'wxyz' * 32768}
single_file_bytes = max(len(content) for content in entries.values())
for name, content in {**entries, unchanged: b'unchanged data'}.items():
    path = source / name
    makepath(path.parent)
    path.write_bytes(content)
    os.utime(path, (stamp, stamp))

run_rsync('-a', f'{source}/', f'{basis}/')
latest.symlink_to('basis', target_is_directory=True)
for name, content in entries.items():
    (source / name).write_bytes(b'changed\n' * 256 + content[2048:])
    os.utime(source / name, (stamp + 60, stamp + 60))

for mode in ('link-dest', 'copy-dest', 'compare-dest'):
    dest = SCRATCHDIR / mode
    output = run_rsync('-a', '--no-whole-file', '--stats', '--no-human-readable',
                       f'--{mode}={latest}/', f'{source}/', f'{dest}/',
                       capture_output=True).stdout
    match = re.search(r'^Matched data: ([0-9,]+) bytes$', output, re.MULTILINE)
    if not match or int(match.group(1).replace(',', '')) <= single_file_bytes:
        test_fail(f'--{mode} did not reuse blocks from both basis files: {output!r}')
    for name in entries:
        assert_same(source / name, dest / name, label=f'{mode} delta result')
        assert_not_hardlinked(basis / name, dest / name, label=f'{mode} changed file')
    if mode == 'compare-dest':
        assert_not_exists(dest / unchanged, label='unchanged compare-dest file')
        continue
    assert_same(source / unchanged, dest / unchanged, label=f'{mode} unchanged file')
    if mode == 'link-dest':
        assert_hardlinked(basis / unchanged, dest / unchanged, label='unchanged file')
    else:
        assert_not_hardlinked(basis / unchanged, dest / unchanged, label='unchanged copy')

assert_same(source / unchanged, basis / unchanged, label='unchanged basis file')
for name, content in entries.items():
    if (basis / name).read_bytes() != content:
        test_fail(f'delta update modified the basis file {name}')

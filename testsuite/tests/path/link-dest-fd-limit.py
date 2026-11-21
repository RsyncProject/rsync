#!/usr/bin/env python3

import os
import re
import subprocess
import sys

from harness import metadata
from harness.environment import SCRATCHDIR
from harness.filesystem import assert_hardlinked, assert_not_hardlinked, assert_same, makepath
from harness.results import test_fail
from harness.rsync import rsync_argv, run_rsync

metadata(features={'hardlinks', 'symlink'}, transports={'pipe'}, mutates={'filesystem'},
         tags={'path', 'regression'})

source = SCRATCHDIR / 'source'
basis = SCRATCHDIR / 'basis'
latest = SCRATCHDIR / 'latest'
dest = SCRATCHDIR / 'destination'
branches = ('alpha', 'beta')
deep = '/'.join(f'd{index}' for index in range(1, 20))
stamp = 1_600_000_000

for branch in branches:
    parent = source / deep / branch / 'leaf'
    makepath(parent)
    line = f'stable original data {branch}\n'.encode()
    content = (line * (1 + 131072 // len(line)))[:131072]
    (parent / 'same').write_text(f'unchanged {branch}\n')
    (parent / 'data').write_bytes(content)
    os.utime(parent / 'same', (stamp, stamp))
    os.utime(parent / 'data', (stamp, stamp))

run_rsync('-a', f'{source}/', f'{basis}/')
latest.symlink_to('basis', target_is_directory=True)
for branch in branches:
    path = source / deep / branch / 'leaf/data'
    with path.open('r+b') as stream:
        stream.write(b'changed block\n')
    os.utime(path, (stamp + 60, stamp + 60))

args = ('-a', '--no-whole-file', '--stats', '--no-human-readable',
        f'--link-dest={latest}/', f'{source}/', f'{dest}/')
if sys.platform.startswith('linux'):
    import resource

    def limit_descriptors():
        resource.setrlimit(resource.RLIMIT_NOFILE, (30, 30))

    spare = os.open(os.devnull, os.O_RDONLY)
    try:
        result = subprocess.run(rsync_argv(*args), capture_output=True, text=True,
                                timeout=30, preexec_fn=limit_descriptors, pass_fds=(spare,))
    finally:
        os.close(spare)
    if result.returncode:
        test_fail(f'link-dest transfer failed with 30 file descriptors: {result.stderr}')
    output = result.stdout
else:
    output = run_rsync(*args, capture_output=True).stdout

match = re.search(r'^Matched data: ([0-9,]+) bytes$', output, re.MULTILINE)
if not match or int(match.group(1).replace(',', '')) <= 131072:
    test_fail(f'changed files did not use both alternate bases: {output!r}')
for branch in branches:
    relative = f'{deep}/{branch}/leaf'
    for name in ('same', 'data'):
        assert_same(source / relative / name, dest / relative / name, label=branch)
    assert_hardlinked(basis / relative / 'same', dest / relative / 'same',
                      label='unchanged basis')
    assert_not_hardlinked(basis / relative / 'data', dest / relative / 'data',
                          label='changed data')

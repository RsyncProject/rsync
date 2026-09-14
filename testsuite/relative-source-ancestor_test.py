#!/usr/bin/env python3
"""Absolute --relative sources with a trusted-owned ancestor symlink."""

import os
import pwd
import subprocess

from rsyncfns import SCRATCHDIR, rsync_argv, test_fail

real = SCRATCHDIR / 'real'
source = real / 'My_Documents'
source.mkdir(parents=True)
(source / 'marker').write_text('source contents\n')
link = SCRATCHDIR / 'home'
os.symlink(str(real), link)

for index, options in enumerate((('-a',), ('-aR',), ('-aR', '--no-inc-recursive'))):
    for trailing in ('', '/'):
        dest = SCRATCHDIR / f'dest-{index}-{bool(trailing)}'
        dest.mkdir()
        proc = subprocess.run(
            rsync_argv(*options, str(link / 'My_Documents') + trailing, str(dest) + '/'),
            capture_output=True, text=True,
        )
        if proc.returncode:
            test_fail(f'trusted ancestor transfer failed: {proc.stdout}{proc.stderr}')
        if '-aR' in options:
            expected = dest / str(link / 'My_Documents').lstrip('/') / 'marker'
        else:
            expected = dest / ('' if trailing else 'My_Documents') / 'marker'
        if not expected.is_file() or expected.read_text() != 'source contents\n':
            test_fail('relative source layout or contents changed')

dest = SCRATCHDIR / 'remove-dest'
dest.mkdir()
proc = subprocess.run(
    rsync_argv('-aR', '--remove-source-files', str(link / 'My_Documents') + '/', str(dest) + '/'),
    capture_output=True, text=True,
)
expected = dest / str(link / 'My_Documents').lstrip('/') / 'marker'
if proc.returncode or (source / 'marker').exists() or not expected.is_file():
    test_fail(f'remove-source-files failed: {proc.stdout}{proc.stderr}')
(source / 'marker').write_text('source contents\n')

if os.geteuid() == 0:
    attacker = next((entry.pw_uid for entry in pwd.getpwall() if entry.pw_uid != 0), None)
    if attacker is not None:
        os.lchown(link, attacker, -1)
        dest = SCRATCHDIR / 'untrusted-dest'
        dest.mkdir()
        proc = subprocess.run(
            rsync_argv('-aR', str(link / 'My_Documents') + '/', str(dest) + '/'),
            capture_output=True, text=True,
        )
        if proc.returncode == 0 or any(dest.rglob('marker')):
            test_fail('an untrusted ancestor symlink was followed')

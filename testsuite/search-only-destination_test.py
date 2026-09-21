#!/usr/bin/env python3
"""The receiver must traverse a searchable but unreadable destination parent.

Android exposes /sdcard through such a path, so the race-safe destination walk
must use directory descriptors that require search permission only.
"""

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from rsyncfns import SCRATCHDIR, rmtree, rsync_argv, test_fail, test_skipped

if not sys.platform.startswith('linux'):
    test_skipped('search-only-destination is Linux-specific')

launcher = []
if os.geteuid() == 0:
    setpriv = shutil.which('setpriv')
    if setpriv is None:
        test_skipped('setpriv is unavailable for the root-run testsuite')
    launcher = [setpriv, '--reuid=65534', '--regid=65534', '--clear-groups']

external_base = os.geteuid() == 0
if external_base:
    base = Path(tempfile.mkdtemp(prefix='rsync-search-only-'))
    base.chmod(0o755)
else:
    base = SCRATCHDIR / 'search-only-destination'
src = base / 'src'
parent = base / 'search-only'
dest = parent / 'dest'
rmtree(base)
src.mkdir(parents=True)
dest.mkdir(parents=True)
(src / 'probe').write_text('search-only destination\n')

if os.geteuid() == 0:
    for path in (src, src / 'probe', dest):
        os.chown(path, 65534, 65534)

try:
    parent.chmod(0o111)
    try:
        probe = subprocess.run(
            launcher + ['test', '-r', str(parent)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if probe.returncode == 0:
            test_skipped('filesystem does not enforce the search-only test mode')
        if probe.returncode != 1:
            test_fail(f'search-only permission probe failed with exit {probe.returncode}')

        proc = subprocess.run(
            launcher + rsync_argv('-a', f'{src}/', f'{dest}/'),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    finally:
        parent.chmod(0o755)
    copied = (dest / 'probe').read_text() if (dest / 'probe').is_file() else None
finally:
    if external_base:
        rmtree(base)

if proc.returncode != 0:
    test_fail(
        'receiver could not enter a destination below a searchable, unreadable '
        f'parent (exit {proc.returncode}): {proc.stderr.strip()}'
    )
if copied != 'search-only destination\n':
    test_fail('receiver did not copy into the search-only destination')

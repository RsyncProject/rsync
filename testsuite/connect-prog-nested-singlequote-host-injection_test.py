#!/usr/bin/env python3
"""RSYNC_CONNECT_PROG host quoting composes unsafely with single quotes."""

import os
import subprocess
from pathlib import Path

from rsyncfns import SCRATCHDIR, makepath, rsync_argv, test_fail

sentinel = Path.cwd() / 'connect-prog-nested-pwned'
try:
    sentinel.unlink()
except FileNotFoundError:
    pass
makepath(SCRATCHDIR / 'dest')

old = os.environ.get('RSYNC_CONNECT_PROG')
os.environ['RSYNC_CONNECT_PROG'] = "sh -c 'printf %H >/dev/null'"
try:
    host = f'localhost;touch${{IFS}}{sentinel.name};#'
    subprocess.run(
        rsync_argv('-r', f'rsync://{host}/m/', f'{SCRATCHDIR}/dest/'),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
finally:
    if old is None:
        os.environ.pop('RSYNC_CONNECT_PROG', None)
    else:
        os.environ['RSYNC_CONNECT_PROG'] = old

if sentinel.exists():
    sentinel.unlink()
    test_fail('RSYNC_CONNECT_PROG host escaped the template quote context')
print('RSYNC_CONNECT_PROG rejected a shell-active host')

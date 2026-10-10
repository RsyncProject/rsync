#!/usr/bin/env python3

import datetime
import os
import platform
import shlex
import subprocess

from harness import rsync
from harness.rsync import FROMDIR, TMPDIR, TODIR, TOOLDIR, run_rsync, test_fail, test_skipped

vv = run_rsync('-VV', check=True, capture_output=True)
if '"atimes": true' not in vv.stdout:
    test_skipped("Rsync is configured without atimes support", capability='noatime')

if platform.system() != 'Linux':
    test_skipped("O_NOATIME is only supported on Linux", capability='noatime')

FROMDIR.mkdir(parents=True, exist_ok=True)
foo = FROMDIR / 'foo'
foo.write_text("content\n")

atime = datetime.datetime(2001, 2, 3, 17, 17, 42).timestamp()
mtime = foo.stat().st_mtime
os.utime(foo, (atime, mtime))

rsync.TLS_ARGS = '--atimes'

def _tls_listing(path: str) -> str:
    cmd = [str(TOOLDIR / 'tls')] + shlex.split(rsync.TLS_ARGS) + [str(path)]
    return subprocess.check_output(cmd, text=True)

before = _tls_listing(foo)
(TMPDIR / 'atime-from-before').write_text(before)

run_rsync('--open-noatime', '--archive', '--recursive', '--times',
          '--atimes', '-vvv', f'{FROMDIR}/', f'{TODIR}/')

after = _tls_listing(foo)
(TMPDIR / 'atime-from-after').write_text(after)

if before != after:
    diff = subprocess.run(
        ['diff', '-u',
         str(TMPDIR / 'atime-from-before'),
         str(TMPDIR / 'atime-from-after')],
        capture_output=True, text=True,
    )
    print(diff.stdout)
    test_fail("source atime changed across rsync --open-noatime run")

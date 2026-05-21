#!/usr/bin/env python3
# Python rewrite of testsuite/open-noatime.test.
#
# Test that rsync --open-noatime keeps the source atime intact across the
# transfer.  --open-noatime did not work properly on files with size > 0
# at one point, so the test uses a non-empty source file.

import datetime
import os
import platform
import shlex
import subprocess

import rsyncfns
from rsyncfns import FROMDIR, TMPDIR, TODIR, TOOLDIR, run_rsync, test_fail, test_skipped


vv = run_rsync('-VV', check=True, capture_output=True)
if '"atimes": true' not in vv.stdout:
    test_skipped("Rsync is configured without atimes support")

# O_NOATIME is Linux-specific.
if platform.system() != 'Linux':
    test_skipped("O_NOATIME is only supported on Linux")

FROMDIR.mkdir(parents=True, exist_ok=True)
foo = FROMDIR / 'foo'
foo.write_text("content\n")

# Pin the source atime to a known historical value (mtime preserved).
atime = datetime.datetime(2001, 2, 3, 17, 17, 42).timestamp()
mtime = foo.stat().st_mtime
os.utime(foo, (atime, mtime))

rsyncfns.TLS_ARGS = '--atimes'

# Capture the atime of the source via tls BEFORE the rsync run.
def _tls_listing(path: str) -> str:
    cmd = [str(TOOLDIR / 'tls')] + shlex.split(rsyncfns.TLS_ARGS) + [str(path)]
    return subprocess.check_output(cmd, text=True)


before = _tls_listing(foo)
(TMPDIR / 'atime-from-before').write_text(before)

# Don't use checkit() here -- the file-content diff it does would update
# atimes on the source and defeat the test.
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

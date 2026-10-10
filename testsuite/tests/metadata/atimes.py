#!/usr/bin/env python3

import datetime
import os

from harness import rsync
from harness.rsync import FROMDIR, TODIR, checkit, run_rsync, test_skipped

vv = run_rsync('-VV', check=True, capture_output=True)
if '"atimes": true' not in vv.stdout:
    test_skipped("Rsync is configured without atimes support")

FROMDIR.mkdir(parents=True, exist_ok=True)
foo = FROMDIR / 'foo'
foo.touch()

atime = datetime.datetime(2001, 2, 3, 17, 17, 42).timestamp()
mtime = foo.stat().st_mtime
os.utime(foo, (atime, mtime))

rsync.TLS_ARGS = '--atimes'

checkit(['-rtUgvvv', f'{FROMDIR}/', f'{TODIR}/'], FROMDIR, TODIR)

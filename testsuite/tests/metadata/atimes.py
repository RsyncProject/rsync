#!/usr/bin/env python3

import datetime
import os

from harness import environment
from harness.comparison import checkit
from harness.environment import FROMDIR, TODIR
from harness.results import test_skipped
from harness.rsync import run_rsync
from harness import metadata

metadata(transports={'pipe'})

vv = run_rsync('-VV', check=True, capture_output=True)
if '"atimes": true' not in vv.stdout:
    test_skipped("Rsync is configured without atimes support")

FROMDIR.mkdir(parents=True, exist_ok=True)
foo = FROMDIR / 'foo'
foo.touch()

atime = datetime.datetime(2001, 2, 3, 17, 17, 42).timestamp()
mtime = foo.stat().st_mtime
os.utime(foo, (atime, mtime))

environment.TLS_ARGS = '--atimes'

checkit(['-rtUgvvv', f'{FROMDIR}/', f'{TODIR}/'], FROMDIR, TODIR)

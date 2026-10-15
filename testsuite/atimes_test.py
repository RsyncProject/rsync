#!/usr/bin/env python3
# Python rewrite of testsuite/atimes.test.
#
# Test that rsync preserves source atimes when the binary was built with
# atimes support. We pin the source file's atime to a known historical
# value then sync with -U; the listing (with --atimes in tls) must match
# between source and destination.

import os

import rsyncfns
from rsyncfns import FROMDIR, TODIR, checkit, run_rsync, test_skipped


vv = run_rsync('-VV', check=True, capture_output=True)
if '"atimes": true' not in vv.stdout:
    test_skipped("Rsync is configured without atimes support")

FROMDIR.mkdir(parents=True, exist_ok=True)
foo = FROMDIR / 'foo'
foo.touch()

# `touch -a -t 200102031717.42` -> set atime to 2001-02-03 17:17:42, mtime unchanged.
import datetime
atime = datetime.datetime(2001, 2, 3, 17, 17, 42).timestamp()
mtime = foo.stat().st_mtime
os.utime(foo, (atime, mtime))

# Make the listing include atimes so checkit's tls compare picks up the
# transferred atime.
rsyncfns.TLS_ARGS = '--atimes'

checkit(['-rtUgvvv', f'{FROMDIR}/', f'{TODIR}/'], FROMDIR, TODIR)

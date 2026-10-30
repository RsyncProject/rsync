#!/usr/bin/env python3
# Python rewrite of testsuite/crtimes.test.
#
# Test that rsync preserves source create times when the binary was built
# with crtimes support. Touch tricks: setting an older time via touch sets
# the create time to the mtime; setting a newer time affects only mtime.

import datetime
import os

import rsyncfns
from rsyncfns import FROMDIR, TODIR, checkit, run_rsync, test_skipped


vv = run_rsync('-VV', check=True, capture_output=True)
if '"crtimes": true' not in vv.stdout:
    test_skipped("Rsync is configured without crtimes support")

FROMDIR.mkdir(parents=True, exist_ok=True)
(FROMDIR / 'foo').write_text("hiho\n")


def _utime(path, when: 'datetime.datetime') -> None:
    ts = when.timestamp()
    os.utime(path, (ts, ts))


# Touch fromdir to an old time then to a newer time -- in shells with the
# right kernel support this leaves the create time pinned to the older.
_utime(FROMDIR, datetime.datetime(2001, 1, 1, 11, 11, 11))
_utime(FROMDIR, datetime.datetime(2002, 2, 2, 22, 22, 22))

_utime(FROMDIR / 'foo', datetime.datetime(2001, 11, 11, 11, 11, 11))
_utime(FROMDIR / 'foo', datetime.datetime(2002, 12, 12, 22, 22, 22))

rsyncfns.TLS_ARGS = '--crtimes'

checkit(['-rtgvvv', '--crtimes', f'{FROMDIR}/', f'{TODIR}/'], FROMDIR, TODIR)

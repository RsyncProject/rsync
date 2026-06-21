#!/usr/bin/env python3
from rsyncfns import SCRATCHDIR, rsync_argv
from rsyncfns import expect_fail

expect_fail(
    rsync_argv('--max-alloc=0', str(SCRATCHDIR / 'missing-src'), str(SCRATCHDIR / 'missing-dst')),
    'max-alloc must be greater than zero',
)
print("max-alloc-zero-rejected: --max-alloc=0 is rejected")

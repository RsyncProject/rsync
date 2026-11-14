#!/usr/bin/env python3
# Python rewrite of testsuite/fuzzy.test.
#
# Test --fuzzy: with a matching-content file already in the destination
# under a different name, rsync should use it as a basis for the new name
# instead of re-transferring (and --delete-delay still removes the stale
# basis file at the end).

import time

from rsyncfns import (
    FROMDIR, SRCDIR, TODIR, cp_p, cp_touch, run_rsync, test_fail, verify_dirs,
)


FROMDIR.mkdir(parents=True, exist_ok=True)
TODIR.mkdir(parents=True, exist_ok=True)

cp_p(SRCDIR / 'rsync.c', FROMDIR / 'rsync.c')
cp_touch(FROMDIR / 'rsync.c', TODIR / 'rsync2.c')
time.sleep(1)

# Drive rsync directly (rather than checkit) so we can capture --debug=FUZZY:
# a final tree match alone would also be produced by a full transfer that
# ignored --fuzzy, so assert the generator actually picked rsync2.c as the
# fuzzy basis for rsync.c (generator.c find_fuzzy / "fuzzy basis selected").
proc = run_rsync('-avvi', '--no-whole-file', '--fuzzy', '--delete-delay',
                 '--debug=FUZZY', f'{FROMDIR}/', f'{TODIR}/',
                 capture_output=True)
if 'fuzzy basis selected for rsync.c: rsync2.c' not in proc.stdout:
    test_fail("--fuzzy did not select rsync2.c as the basis for rsync.c; "
              f"--debug=FUZZY output was:\n{proc.stdout}")
# ...and --delete-delay still removes the stale basis, leaving TODIR == FROMDIR.
verify_dirs(FROMDIR, TODIR)

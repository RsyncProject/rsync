#!/usr/bin/env python3

from harness.rsync import (
    FROMDIR, SRCDIR, TODIR, cp_p, cp_touch, run_rsync, test_fail, verify_dirs,
)

FROMDIR.mkdir(parents=True, exist_ok=True)
TODIR.mkdir(parents=True, exist_ok=True)

cp_p(SRCDIR / 'rsync.c', FROMDIR / 'rsync.c')
cp_touch(FROMDIR / 'rsync.c', TODIR / 'rsync2.c')

proc = run_rsync('-avvi', '--no-whole-file', '--fuzzy', '--delete-delay',
                 '--debug=FUZZY', f'{FROMDIR}/', f'{TODIR}/',
                 capture_output=True)
if 'fuzzy basis selected for rsync.c: rsync2.c' not in proc.stdout:
    test_fail("--fuzzy did not select rsync2.c as the basis for rsync.c; "
              f"--debug=FUZZY output was:\n{proc.stdout}")
verify_dirs(FROMDIR, TODIR)

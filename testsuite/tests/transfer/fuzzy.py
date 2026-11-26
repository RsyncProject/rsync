#!/usr/bin/env python3

from shutil import copy2

from harness.comparison import verify_dirs
from harness.environment import FROMDIR, SRCDIR, TODIR
from harness.filesystem import cp_touch
from harness.results import test_fail
from harness.rsync import run_rsync
from harness import metadata

metadata(transports={'pipe'})

FROMDIR.mkdir(parents=True, exist_ok=True)
TODIR.mkdir(parents=True, exist_ok=True)

copy2(SRCDIR / 'rsync.c', FROMDIR / 'rsync.c')
cp_touch(FROMDIR / 'rsync.c', TODIR / 'rsync2.c')

proc = run_rsync('-avvi', '--no-whole-file', '--fuzzy', '--delete-delay',
                 '--debug=FUZZY', f'{FROMDIR}/', f'{TODIR}/',
                 capture_output=True)
if 'fuzzy basis selected for rsync.c: rsync2.c' not in proc.stdout:
    test_fail("--fuzzy did not select rsync2.c as the basis for rsync.c; "
              f"--debug=FUZZY output was:\n{proc.stdout}")
verify_dirs(FROMDIR, TODIR)

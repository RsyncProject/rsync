#!/usr/bin/env python3
# Python rewrite of testsuite/fuzzy.test.
#
# Test --fuzzy: with a matching-content file already in the destination
# under a different name, rsync should use it as a basis for the new name
# instead of re-transferring (and --delete-delay still removes the stale
# basis file at the end).

import time

from rsyncfns import FROMDIR, SRCDIR, TODIR, checkit, cp_p, cp_touch


FROMDIR.mkdir(parents=True, exist_ok=True)
TODIR.mkdir(parents=True, exist_ok=True)

cp_p(SRCDIR / 'rsync.c', FROMDIR / 'rsync.c')
cp_touch(FROMDIR / 'rsync.c', TODIR / 'rsync2.c')
time.sleep(1)

checkit(['-avvi', '--no-whole-file', '--fuzzy', '--delete-delay',
         f'{FROMDIR}/', f'{TODIR}/'], FROMDIR, TODIR)

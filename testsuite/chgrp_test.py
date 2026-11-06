#!/usr/bin/env python3
# Python rewrite of testsuite/chgrp.test.
#
# Test that -g preserves group ownership when the user is a member of the
# target group. Creates one file per supplementary group, chgrps each,
# then verifies the destination listing matches.

import os
import shutil
import time

from rsyncfns import FROMDIR, TODIR, checkit, rsync_getgroups, test_fail


groups = rsync_getgroups()
if not groups:
    test_fail("Can't get groups")

FROMDIR.mkdir(parents=True, exist_ok=True)
for g in groups:
    fname = FROMDIR / f'foo-{g}'
    fname.write_text(time.ctime() + '\n')
    chgrp = shutil.which('chgrp')
    if chgrp is None:
        test_fail("chgrp not found in PATH")
    # The shell test treats chgrp failure as fatal.
    try:
        os.chown(fname, -1, int(g))
    except (ValueError, PermissionError):
        # If g isn't numeric or we lack permission, fall back to chgrp(1).
        import subprocess
        proc = subprocess.run([chgrp, g, str(fname)])
        if proc.returncode != 0:
            test_fail("Can't chgrp")

time.sleep(2)

checkit(['-rtgpvvv', f'{FROMDIR}/', f'{TODIR}/'], FROMDIR, TODIR)

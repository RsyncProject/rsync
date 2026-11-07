#!/usr/bin/env python3

import os
import shutil
import time

from harness.rsync import FROMDIR, TODIR, checkit, rsync_getgroups, test_fail

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
    try:
        os.chown(fname, -1, int(g))
    except (ValueError, PermissionError):
        import subprocess
        proc = subprocess.run([chgrp, g, str(fname)])
        if proc.returncode != 0:
            test_fail("Can't chgrp")

checkit(['-rtgpvvv', f'{FROMDIR}/', f'{TODIR}/'], FROMDIR, TODIR)

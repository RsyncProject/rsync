#!/usr/bin/env python3

import errno
import os

from harness.rsync import FROMDIR, TODIR, check_perms, run_rsync, test_skipped

FROMDIR.mkdir(parents=True, exist_ok=True)
(FROMDIR / '1').write_text("#!/bin/sh\necho 'Program One!'\n")
(FROMDIR / '2').write_text("#!/bin/sh\necho 'Program Two!'\n")

_STICKY_SKIP_ERRNOS = {errno.EPERM, errno.EACCES, getattr(errno, 'EFTYPE', None)}
try:
    os.chmod(FROMDIR / '1', 0o1700)
except OSError as e:
    if e.errno not in _STICKY_SKIP_ERRNOS:
        raise
    test_skipped("Can't chmod")
os.chmod(FROMDIR / '2', 0o600)

run_rsync('-rvv', f'{FROMDIR}/', f'{TODIR}/')

check_perms(TODIR / '1', 'rwx------')
check_perms(TODIR / '2', 'rw-------')

os.chmod(FROMDIR / '1', 0o600)
os.chmod(FROMDIR / '2', 0o601)
os.chmod(TODIR / '2', 0o604)

run_rsync('-rvv', f'{FROMDIR}/', f'{TODIR}/')

check_perms(TODIR / '1', 'rwx------')
check_perms(TODIR / '2', 'rw----r--')

run_rsync('-rvvE', f'{FROMDIR}/', f'{TODIR}/')

check_perms(TODIR / '1', 'rw-------')
check_perms(TODIR / '2', 'rwx---r-x')

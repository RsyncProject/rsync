#!/usr/bin/env python3

import os

from harness.rsync import FROMDIR, TODIR, check_perms, run_rsync

FROMDIR.mkdir(parents=True, exist_ok=True)
(FROMDIR / '1').write_text("#!/bin/sh\necho 'Program One!'\n")
(FROMDIR / '2').write_text("#!/bin/sh\necho 'Program Two!'\n")

os.chmod(FROMDIR / '1', 0o700)
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

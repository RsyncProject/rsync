#!/usr/bin/env python3
# Python rewrite of testsuite/executability.test.
#
# Test --executability (-E): -E should propagate only the executable bits
# from source to destination (other permission changes ignored), while a
# normal copy without -E should leave the destination permissions alone.

import errno
import os

from rsyncfns import FROMDIR, TODIR, check_perms, run_rsync, test_skipped


FROMDIR.mkdir(parents=True, exist_ok=True)
(FROMDIR / '1').write_text("#!/bin/sh\necho 'Program One!'\n")
(FROMDIR / '2').write_text("#!/bin/sh\necho 'Program Two!'\n")

# Setuid-and-rwx for owner, nothing else. Some platforms reject 1700 for
# non-root callers (no permission to set sticky); FreeBSD rejects it with
# EFTYPE rather than EPERM. Only skip on those; re-raise anything unexpected.
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

# Permute the source/destination perms; without -E nothing should change.
os.chmod(FROMDIR / '1', 0o600)
os.chmod(FROMDIR / '2', 0o601)
os.chmod(TODIR / '2', 0o604)

run_rsync('-rvv', f'{FROMDIR}/', f'{TODIR}/')

check_perms(TODIR / '1', 'rwx------')
check_perms(TODIR / '2', 'rw----r--')

# Now with -E: 1 loses its x (source has 600), 2 gains x (source has 601).
run_rsync('-rvvE', f'{FROMDIR}/', f'{TODIR}/')

check_perms(TODIR / '1', 'rw-------')
check_perms(TODIR / '2', 'rwx---r-x')

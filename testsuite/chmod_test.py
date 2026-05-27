#!/usr/bin/env python3
# Python rewrite of testsuite/chmod.test.
#
# Test that varied read-only and set[ug]id permissions transfer correctly
# both on first copy (whole-file) and on subsequent updates (delta).

import os

from rsyncfns import FROMDIR, TODIR, checkit, hands_setup


hands_setup()

# Three of these chmod modes use the sticky/setuid/setgid bits which some
# platforms refuse for non-root. The shell test tries them in descending
# order, falling back to plain mode on rejection.
def _try_chmods(path, modes):
    for m in modes:
        try:
            os.chmod(path, m)
            return
        except PermissionError:
            continue
    # Final mode in the list is the no-special-bits fallback.
    os.chmod(path, modes[-1])


os.chmod(FROMDIR / 'text', 0o440)
os.chmod(FROMDIR / 'dir' / 'text', 0o500)
_try_chmods(FROMDIR / 'dir' / 'subdir' / 'foobar.baz',
            [0o6450, 0o2450, 0o1450, 0o450])
_try_chmods(FROMDIR / 'dir' / 'subdir' / 'subsubdir' / 'etc-ltr-list',
            [0o2670, 0o1670, 0o670])

# First a normal whole-file copy.
checkit(['-avv', f'{FROMDIR}/', str(TODIR)], FROMDIR, TODIR)

# Then update through delta with -I (ignore times) so every file is
# touched again.
checkit(['-avvI', '--no-whole-file', f'{FROMDIR}/', str(TODIR)], FROMDIR, TODIR)

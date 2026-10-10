#!/usr/bin/env python3

import os

from harness.rsync import FROMDIR, TODIR, checkit, hands_setup, set_supported_mode

hands_setup()

os.chmod(FROMDIR / 'text', 0o440)
os.chmod(FROMDIR / 'dir' / 'text', 0o500)
set_supported_mode(FROMDIR / 'dir' / 'subdir' / 'foobar.baz',
                   [0o6450, 0o2450, 0o1450, 0o450])
set_supported_mode(FROMDIR / 'dir' / 'subdir' / 'subsubdir' / 'etc-ltr-list',
                   [0o2670, 0o1670, 0o670])

checkit(['-avv', f'{FROMDIR}/', str(TODIR)], FROMDIR, TODIR)

checkit(['-avvI', '--no-whole-file', f'{FROMDIR}/', str(TODIR)], FROMDIR, TODIR)

#!/usr/bin/env python3

import os
import subprocess

from harness.rsync import (FROMDIR, SCRATCHDIR, TODIR, TOOLDIR, checkit, hands_setup,
                      set_supported_mode, test_skipped)

def _fsdev(path: str) -> str:
    return subprocess.check_output(
        [str(TOOLDIR / 'getfsdev'), path], text=True,
    ).strip()

hands_setup()

scratch_dev = _fsdev(str(SCRATCHDIR))
tmpdir2 = None
candidates = [
    os.environ.get('RSYNC_TEST_TMP', '/override-tmp-not-specified'),
    '/run/shm', '/var/tmp', '/tmp',
]
for cand in candidates:
    if not (os.path.isdir(cand) and os.access(cand, os.W_OK)):
        continue
    if _fsdev(cand) != scratch_dev:
        tmpdir2 = cand
        break

if tmpdir2 is None:
    test_skipped("Can't find a tmp dir on a different file system", capability='cross_device')

os.chmod(FROMDIR / 'text', 0o440)
os.chmod(FROMDIR / 'dir' / 'text', 0o500)
set_supported_mode(FROMDIR / 'dir' / 'subdir' / 'foobar.baz',
                   [0o6450, 0o2450, 0o1450, 0o450])
set_supported_mode(FROMDIR / 'dir' / 'subdir' / 'subsubdir' / 'etc-ltr-list',
                   [0o2670, 0o1670, 0o670])

checkit(['-avv', f'--temp-dir={tmpdir2}', f'{FROMDIR}/', str(TODIR)],
        FROMDIR, TODIR)

checkit(['-avvI', '--no-whole-file', f'--temp-dir={tmpdir2}',
         f'{FROMDIR}/', str(TODIR)], FROMDIR, TODIR)

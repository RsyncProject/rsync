#!/usr/bin/env python3
# Python rewrite of testsuite/chmod-temp-dir.test.
#
# Like chmod_test.py, but uses --temp-dir pointing at a different
# filesystem so rsync must rename(2) across filesystems (i.e. fall back
# to copy+unlink) instead of the in-place rename it does when temp and
# destination are on the same fs. We probe candidate tmp paths to find
# one whose filesystem differs from the scratch dir.

import os
import subprocess

from rsyncfns import FROMDIR, SCRATCHDIR, TODIR, TOOLDIR, checkit, hands_setup, test_skipped


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
    test_skipped("Can't find a tmp dir on a different file system")


# Mirror chmod_test.py: set up a varied permission tree on the source.
def _try_chmods(path, modes):
    for m in modes:
        try:
            os.chmod(path, m)
            return
        except PermissionError:
            continue
    os.chmod(path, modes[-1])


os.chmod(FROMDIR / 'text', 0o440)
os.chmod(FROMDIR / 'dir' / 'text', 0o500)
_try_chmods(FROMDIR / 'dir' / 'subdir' / 'foobar.baz',
            [0o6450, 0o2450, 0o1450, 0o450])
_try_chmods(FROMDIR / 'dir' / 'subdir' / 'subsubdir' / 'etc-ltr-list',
            [0o2670, 0o1670, 0o670])

# First a normal copy (whole-file) but through a cross-fs --temp-dir.
checkit(['-avv', f'--temp-dir={tmpdir2}', f'{FROMDIR}/', str(TODIR)],
        FROMDIR, TODIR)

# Then an update through delta, still routing partial transfers across fs.
checkit(['-avvI', '--no-whole-file', f'--temp-dir={tmpdir2}',
         f'{FROMDIR}/', str(TODIR)], FROMDIR, TODIR)

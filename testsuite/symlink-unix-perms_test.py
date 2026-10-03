#!/usr/bin/env python3

import errno
import os
import random
import stat
import sys

from rsyncfns import (
    FROMDIR, SCRATCHDIR, TODIR,
    assert_mode, rmtree, run_rsync, test_fail, test_skipped
)

#   Put some error messages into variable to make later code more readable.

error = 'error occurred during test of symlink access change capability: '
notimplemented = 'symlink Unix access change is not implemented on this system'
notsupported = 'symlink Unix access change is not supported on this system'

#   Make sure that the FROMDIR and TODIR directories exist. These are the
#   source and destination directories, respectively, for the test.

os.chdir(SCRATCHDIR)

FROMDIR.mkdir(parents=True, exist_ok=True)
TODIR.mkdir(parents=True, exist_ok=True)

#   Create a test directory and a test file in FROMDIR for use later as symlink
#   targets, saving the actual names of each in a variable that will also be
#   used later.

testdirectory = 'testdirectory'
testfile = 'testfile'

(FROMDIR / testdirectory).mkdir(parents=True)
(FROMDIR / testfile).touch()

#   See if the system permits setting Unix access permssions on symlinks.
#   If not, there's no point in continuing.
#
#   To test this, the following code creates a symlink in FROMDIR that points
#   to the test directory there. It then tries to change the Unix access
#   permission for that symlink. If the result is a "not implemented" or a
#   "not supported" error, the actual test is skipped. If any other error is
#   returned, the test reports failure.

os.chdir(FROMDIR)

os.symlink(testdirectory, 'symlink', target_is_directory=True)

try:
    os.chmod('symlink', stat.S_IREAD + stat.S_IXOTH, follow_symlinks=False)

except NotImplementedError:
    test_skipped(notimplemented)

except OSError as e:
    if (e.errno == errno.ENOTSUP):
        test_skipped(notsupported)
    test_fail('OS' + error + f'{e}')

except Exception as e:
    test_fail(error + f'{e}')

#   The symlink access change attempt reported success. Make sure that the
#   Unix access permissions were actually changed. If not, the test fails.

assert_mode('symlink', stat.S_IREAD + stat.S_IXOTH)

#   The system can change symlink Unix access permissions. Continue ...

os.remove('symlink')

os.chdir(SCRATCHDIR)

#   Create a number of symlinks to use for testing rsync.
#
#   Set the Unix access permissions for those symlinks to selected values.
#   The selected values have no specific agenda. However, owner read access
#   is always maintained to ensure that stat info is readable.

#   The following array consists of a number of targets and associated Unix
#   access permissions that will be used in the actual test. The symlink
#   names will be of the form "symlink-{n}" where "{n}" is the array index
#   for each symlink created.

symlinks = [
    [ testdirectory, 0o770 ],
    [ testfile, 0o0444 ],
    [ testfile, 0o0711 ],
    [ testfile, 0o0777 ],
    [ testdirectory, 0o7777 ],
    [ testfile, 0o0400 ],
    [ testdirectory, 0o1407 ],
    [ testdirectory, 0o2470 ],
    [ testdirectory, 0o4777 ],
    [ testfile, 0o7777 ]
]

#   Actually create the test symlinks in the source directory.

for n, symlink in enumerate(symlinks):
    target, access = symlink

    if target == 'directory':
        isdir = True
    else:
        isdir = False

    os.symlink(f'{target}', FROMDIR / f'symlink-{n}', target_is_directory=f'{isdir}')
    os.chmod(FROMDIR / f'symlink-{n}', access, follow_symlinks=False)

#   Now, use rsync to copy the source directory to the destination.

run_rsync('-av', f'{FROMDIR}/', f'{TODIR}/')

#   Finally, check to make sure that the Unix access permissions on the
#   symlinks in the destination match those in the source. The test fail
#   if any one doesn't match.

for n, symlink in enumerate(symlinks):
    expected = stat.S_IMODE(os.stat(FROMDIR / f'symlink-{n}', follow_symlinks=False).st_mode)
    assert_mode(TODIR / f'symlink-{n}', expected)

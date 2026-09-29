#!env /usr/bin/python3 -v

import os
import random
import stat
import sys

from rsyncfns import (
    FROMDIR, SCRATCHDIR, TODIR,
    assert_mode, rmtree, run_rsync, test_fail, test_skipped
)

#   Clean out the test-specific scratch space and ensure that the 'from'
#   and 'to' directories exist.

rmtree(SCRATCHDIR)

FROMDIR.mkdir(parents=True)
TODIR.mkdir(parents=True)

#   See if the system permits setting Unix access permssions on symlinks.
#   If not, there's no point in continuing.
#
#   To test this, the following code creates a symlink in the scratch space
#   directory that points to the start of the scratch space directory. It
#   then tries to change the Unix access permission for that link. If that
#   fails, it shows that the system can't change symlink Unix access
#   permissions and the remaining test process is skipped.
    
os.chdir(SCRATCHDIR)

os.symlink('.', 'symlink', target_is_directory=True)

original = os.lstat('symlink').st_mode
    
#   Try to change the Unix access permissions on the symlink just created.
#
#   Ignore any errors that may occur. If an error occurs, it simply means
#   that the system can't set Unix access permissions on a symlink, a fact
#   that will be caught when the upcoming access check shows the same
#   result as the preceeding one.

try:
    os.chmod('symlink', stat.S_IREAD + stat.S_IXOTH, follow_symlinks=False)
except:
    pass

current = os.lstat('symlink').st_mode

os.remove('symlink')

if current == original:
    test_skipped('this system is unable to set symlink Unix access permissions')

#   The system can change symlink Unix access permissions. Continue ...

#   Create a number of symlinks to use for testing rsync.
#   Set the Unix access permissions for those symlinks to arbitrarily chosen
#   values. Note that these permissions will be different each time the test
#   is run.

symlinks = [ f'symlink-{n}' for n in range(10) ]

random.seed()

for symlink in symlinks:
    os.symlink('.', FROMDIR / symlink, target_is_directory=True)
    os.chmod(FROMDIR / symlink, random.randrange(256) + 256, follow_symlinks=False)

#   Use rsync to copy the source directory to the destination.

run_rsync('-av', f'{FROMDIR}/', f'{TODIR}/')

#   Finally, check to make sure that the Unix access permissions on the
#   symlinks in the destination match those in the source.

for symlink in symlinks:
    expected = stat.S_IMODE(os.stat(FROMDIR / symlink, follow_symlinks=False).st_mode)
    assert_mode(TODIR / symlink, expected)

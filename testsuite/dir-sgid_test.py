#!/usr/bin/env python3
# Python rewrite of testsuite/dir-sgid.test.
#
# Check that rsync obeys the setgid bit on the destination's parent
# directory when creating a new directory to hold the transferred files,
# even though that parent directory is outside the transfer itself.

import os
import shutil
import subprocess

from rsyncfns import (
    SCRATCHDIR, check_perms, run_rsync, test_skipped,
)


old_umask = os.umask(0o077)


def testit(dirname, dirperms, file_expected, prog_expected, dir_expected):
    """Mirror shell `testit dirname dirperms file_expected prog_expected dir_expected`."""
    todir = SCRATCHDIR / dirname
    todir.mkdir()
    # dirperms is either an octal int or the symbolic shell form we translate.
    if isinstance(dirperms, int):
        os.chmod(todir, dirperms)
    else:
        subprocess.run(['chmod', dirperms, str(todir)], check=True)

    run_rsync('-rvv', str(SCRATCHDIR / 'dir'),
              str(SCRATCHDIR / 'file'),
              str(SCRATCHDIR / 'program'),
              f'{todir}/to/')

    check_perms(todir / 'to', dir_expected)
    check_perms(todir / 'to' / 'dir', dir_expected)
    check_perms(todir / 'to' / 'file', file_expected)
    check_perms(todir / 'to' / 'program', prog_expected)


# Cygwin's default dir ACL ruins this test; mimic the shell's getfacl skip.
src_dir = SCRATCHDIR / 'dir'
src_dir.mkdir()
try:
    out = subprocess.run(['getfacl', str(src_dir)],
                         capture_output=True, text=True)
    if 'default:user::' in out.stdout:
        test_skipped("The default ACL mode interferes with this test")
except FileNotFoundError:
    pass  # No getfacl -- proceed.

(SCRATCHDIR / 'file').write_text("File!\n")
(SCRATCHDIR / 'program').write_text("#!/bin/sh\n")

try:
    subprocess.run(['chmod', 'u=rwx,g=rw,g+s,o=r', str(src_dir)], check=True)
except subprocess.CalledProcessError:
    test_skipped("Can't chmod")
os.chmod(SCRATCHDIR / 'file', 0o664)
os.chmod(SCRATCHDIR / 'program', 0o775)

if not (os.stat(src_dir).st_mode & 0o2000):
    test_skipped("The directory setgid bit vanished!")

(src_dir / 'blah').mkdir()
if not (os.stat(src_dir / 'blah').st_mode & 0o2000):
    test_skipped("Your filesystem doesn't use directory setgid; maybe it's BSD.")

testit('setgid-off', 0o700, 'rw-------', 'rwx------', 'rwx------')
testit('setgid-on', 'u=rwx,g=rw,g+s,o-rwx', 'rw-------', 'rwx------', 'rwx--S---')

os.umask(old_umask)

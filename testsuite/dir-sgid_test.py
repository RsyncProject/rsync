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
    SCRATCHDIR, check_perms, run_rsync, test_fail, test_skipped,
)


old_umask = os.umask(0o077)

# A secondary group (if the user has one) lets the gid checks below be
# discriminating: a setgid parent makes new dirs inherit the PARENT's group,
# whereas without setgid they get the process's own group.
prim = os.getgid()
alt_gid = next((g for g in os.getgroups() if g != prim), None)


def testit(dirname, dirperms, file_expected, prog_expected, dir_expected, setgid):
    """Mirror shell `testit dirname dirperms file_expected prog_expected dir_expected`."""
    todir = SCRATCHDIR / dirname
    todir.mkdir()
    # For the setgid case, give the parent a distinct group (when available) so
    # the inherited gid differs from the process's; chmod is applied after the
    # chown so the setgid bit survives.
    if setgid and alt_gid is not None:
        os.chown(todir, -1, alt_gid)
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

    # With setgid set, new dirs must inherit the PARENT's group. We only assert
    # the setgid case: it holds on both SysV/Linux and BSD, and (because the
    # parent is given a secondary group above) still proves setgid took effect
    # on Linux. The no-setgid case is deliberately not checked -- the inherited
    # gid is OS-defined there (the process's group on Linux, but always the
    # parent's on BSD), so it's not a portable assertion.
    if setgid:
        expect_gid = os.stat(todir).st_gid
        for sub in ('to', 'to/dir'):
            g = os.stat(todir / sub).st_gid
            if g != expect_gid:
                test_fail(f"{dirname}: {sub} gid is {g}, expected {expect_gid} "
                          "(setgid inheritance)")


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

testit('setgid-off', 0o700, 'rw-------', 'rwx------', 'rwx------', setgid=False)
testit('setgid-on', 'u=rwx,g=rw,g+s,o-rwx', 'rw-------', 'rwx------', 'rwx--S---',
       setgid=True)

os.umask(old_umask)

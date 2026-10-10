#!/usr/bin/env python3

import os
import subprocess

from harness.rsync import (
    SCRATCHDIR, check_perms, run_rsync, test_fail, test_skipped,
)

old_umask = os.umask(0o077)

prim = os.getgid()
alt_gid = next((g for g in os.getgroups() if g != prim), None)

def testit(dirname, dirperms, file_expected, prog_expected, dir_expected, setgid):
    todir = SCRATCHDIR / dirname
    todir.mkdir()
    if setgid and alt_gid is not None:
        os.chown(todir, -1, alt_gid)
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

    if setgid:
        expect_gid = os.stat(todir).st_gid
        for sub in ('to', 'to/dir'):
            g = os.stat(todir / sub).st_gid
            if g != expect_gid:
                test_fail(f"{dirname}: {sub} gid is {g}, expected {expect_gid} "
                          "(setgid inheritance)")

src_dir = SCRATCHDIR / 'dir'
src_dir.mkdir()
try:
    out = subprocess.run(['getfacl', str(src_dir)],
                         capture_output=True, text=True)
    if 'default:user::' in out.stdout:
        test_skipped("The default ACL mode interferes with this test", capability='directory_sgid')
except FileNotFoundError:
    pass

(SCRATCHDIR / 'file').write_text("File!\n")
(SCRATCHDIR / 'program').write_text("#!/bin/sh\n")

try:
    subprocess.run(['chmod', 'u=rwx,g=rw,g+s,o=r', str(src_dir)], check=True)
except subprocess.CalledProcessError:
    test_skipped("Can't chmod", capability='directory_sgid')
os.chmod(SCRATCHDIR / 'file', 0o664)
os.chmod(SCRATCHDIR / 'program', 0o775)

if not (os.stat(src_dir).st_mode & 0o2000):
    test_skipped("The directory setgid bit vanished!", capability='directory_sgid')

(src_dir / 'blah').mkdir()
if not (os.stat(src_dir / 'blah').st_mode & 0o2000):
    test_skipped("Your filesystem doesn't use directory setgid; maybe it's BSD.",
                 capability='directory_sgid')

testit('setgid-off', 0o700, 'rw-------', 'rwx------', 'rwx------', setgid=False)
testit('setgid-on', 'u=rwx,g=rw,g+s,o-rwx', 'rw-------', 'rwx------', 'rwx--S---',
       setgid=True)

os.umask(old_umask)

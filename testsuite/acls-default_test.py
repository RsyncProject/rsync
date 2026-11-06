#!/usr/bin/env python3
# Python rewrite of testsuite/acls-default.test.
#
# Test that rsync obeys POSIX default ACLs on the destination's parent
# directory when creating the transfer's container directory, even
# though that parent is outside the transfer itself.

import os
import re
import shlex
import subprocess

from rsyncfns import (
    SCRATCHDIR,
    check_perms, run_rsync, test_fail, test_skipped,
)


vv = run_rsync('-VV', check=True, capture_output=True)
if '"ACLs": true' not in vv.stdout:
    test_skipped("Rsync is configured without ACL support")

setfacl_nodef = os.environ.get('setfacl_nodef', 'true')
if setfacl_nodef == 'true':
    test_skipped("I don't know how to use your setfacl command")

if '-k' in setfacl_nodef.split():
    seed_opts = ['-dm', 'u::7,g::5,o:5']
else:
    seed_opts = ['-m', 'd:u::7,d:g::5,d:o:5']

# Seed the scratch dir with a default ACL so the upcoming testit() runs
# inherit a known-base; if setfacl rejects this the FS doesn't have ACLs.
proc = subprocess.run(['setfacl', *seed_opts, str(SCRATCHDIR)])
if proc.returncode != 0:
    test_skipped("Your filesystem has ACLs disabled")


def testit(dirname, default_acl, file_expected, prog_expected):
    """Set a default ACL on a destination parent dir, then verify that
    a transfer into a fresh subdir picks up the inherited perms."""
    todir = SCRATCHDIR / dirname
    todir.mkdir()
    # Clear any inherited default ACL first -- and confirm it succeeded, so the
    # no-default-ACL cases can't silently inherit the scratch dir's seeded
    # default ACL and test the wrong base state.
    if subprocess.run(shlex.split(setfacl_nodef) + [str(todir)]).returncode != 0:
        test_fail(f"{dirname}: clearing the inherited default ACL failed")
    if default_acl:
        if '-k' in setfacl_nodef.split():
            opts = ['-dm', default_acl]
        else:
            # Each "u:/g:/o:/m:" prefix becomes "d:u:/d:g:/d:o:/d:m:".
            translated = re.sub(r'([ugom]:)', r'd:\1', default_acl)
            opts = ['-m', translated]
        subprocess.run(['setfacl', *opts, str(todir)], check=True)

    run_rsync('-rvv',
              str(SCRATCHDIR / 'dir'),
              str(SCRATCHDIR / 'file'),
              str(SCRATCHDIR / 'program'),
              f'{todir}/to/')

    check_perms(todir / 'to', prog_expected)
    check_perms(todir / 'to' / 'dir', prog_expected)
    check_perms(todir / 'to' / 'file', file_expected)
    check_perms(todir / 'to' / 'program', prog_expected)

    # get_local_name shouldn't mess up a single-file transfer.
    run_rsync('-rvv',
              str(SCRATCHDIR / 'file'),
              f'{todir}/to/anotherfile')
    check_perms(todir / 'to' / 'anotherfile', file_expected)

    # And the no-regular-file case (sole-dir transfer).
    run_rsync('-rvv',
              f'{SCRATCHDIR / "dir"}/',
              f'{todir}/to/anotherdir/')
    check_perms(todir / 'to' / 'anotherdir', prog_expected)


(SCRATCHDIR / 'dir').mkdir()
(SCRATCHDIR / 'file').write_text("File!\n")
(SCRATCHDIR / 'program').write_text("#!/bin/sh\n")
os.chmod(SCRATCHDIR / 'dir', 0o777)
os.chmod(SCRATCHDIR / 'file', 0o666)
os.chmod(SCRATCHDIR / 'program', 0o777)

os.umask(0o077)
testit('da777', 'u::7,g::7,o:7', 'rw-rw-rw-', 'rwxrwxrwx')
testit('da775', 'u::7,g::7,o:5', 'rw-rw-r--', 'rwxrwxr-x')
testit('da750', 'u::7,g::5,o:0', 'rw-r-----', 'rwxr-x---')
testit('da750mask', 'u::7,u:0:7,g::7,m:5,o:0', 'rw-r-----', 'rwxr-x---')
testit('noda1', '', 'rw-------', 'rwx------')
os.umask(0o000)
testit('noda2', '', 'rw-rw-rw-', 'rwxrwxrwx')
os.umask(0o022)
testit('noda3', '', 'rw-r--r--', 'rwxr-xr-x')

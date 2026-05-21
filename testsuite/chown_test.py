#!/usr/bin/env python3
# Python rewrite of testsuite/chown.test (and, via a symlink installed by
# the Makefile as chown-fake_test.py, of testsuite/chown-fake.test).
#
# Verifies that rsync -a + ownership-preservation sets the destination
# uid/gid to match the source. The "real" variant needs root to chown(2);
# the "fake" variant emulates ownership in the user.rsync.%stat xattr and
# tests --fake-super.

import os
import platform
import shutil
import subprocess
import sys

import rsyncfns
from rsyncfns import (
    FROMDIR, TODIR,
    checkit, run_rsync, test_fail, test_skipped,
)


# Detect fake variant by the script name we were invoked under. The
# Makefile creates chown-fake_test.py as a symlink to this file.
script_name = os.path.basename(sys.argv[0] if sys.argv[0] else __file__)
fake_variant = 'fake' in script_name

if fake_variant:
    # --fake-super needs xattrs support.
    vv = run_rsync('-VV', check=True, capture_output=True)
    if '"xattrs": true' not in vv.stdout:
        test_skipped("Rsync needs xattrs for fake device tests")
    # Augment the RSYNC command and TLS_ARGS so checkit's listing path
    # treats the xattr-encoded ownership as the file's real ownership.
    rsyncfns.RSYNC = rsyncfns.RSYNC + ' --fake-super'
    rsyncfns.TLS_ARGS = (rsyncfns.TLS_ARGS + ' --fake-super').strip()

    if platform.system() != 'Linux':
        test_skipped(
            f"fake chown emulation not implemented for {platform.system()}"
        )

    def chown_or_fake(path, uid, gid):
        # On Linux, store ownership in the user.rsync.%stat xattr -- the
        # format rsync's --fake-super expects.
        stat = os.stat(path)
        mode = stat.st_mode
        # %stat format: "MODE DEV_MAJOR,DEV_MINOR UID:GID"
        value = f"{mode:o} 0,0 {uid}:{gid}".encode()
        os.setxattr(str(path), b'user.rsync.%stat', value)
        return True
else:
    rsyncfns.RSYNC = rsyncfns.RSYNC + ' --super'

    my_uid = os.getuid()
    if my_uid != 0:
        # If a fakeroot binary is in the environment, re-exec ourselves
        # under it -- same trick the shell test used.
        fakeroot_path = os.environ.get('FAKEROOT_PATH')
        if fakeroot_path and os.access(fakeroot_path, os.X_OK):
            print("Let's try re-running the script under fakeroot...")
            os.execv(fakeroot_path, [fakeroot_path, sys.executable, __file__])

    def chown_or_fake(path, uid, gid):
        try:
            os.chown(path, uid, gid)
            return True
        except (PermissionError, OSError):
            return False


FROMDIR.mkdir(parents=True, exist_ok=True)
name1 = FROMDIR / 'name1'
name2 = FROMDIR / 'name2'
name1.write_text("This is the file\n")
name2.write_text("This is the other file\n")

if not chown_or_fake(name1, 5000, 5002):
    test_skipped("Can't chown (probably need root)")
if not chown_or_fake(name2, 5001, 5003):
    test_skipped("Can't chown (probably need root)")

os.chdir(FROMDIR.parent)
checkit(['-aHvv', 'from/', 'to/'], FROMDIR, TODIR)

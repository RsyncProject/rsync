#!/usr/bin/env python3

import os
import sys

from harness import rsync
from harness.rsync import (
    FROMDIR, RSYNC_PREFIX, TODIR,
    checkit, test_skipped, xattr_set, xattrs_supported,
)

script_name = os.path.basename(sys.argv[0] if sys.argv[0] else __file__)
fake_variant = 'fake' in script_name

if fake_variant:
    if not xattrs_supported():
        test_skipped("Rsync needs xattrs for fake device tests", capability='xattr_runtime')
    rsync.RSYNC = rsync.RSYNC + ' --fake-super'
    rsync.TLS_ARGS = (rsync.TLS_ARGS + ' --fake-super').strip()

    def chown_or_fake(path, uid, gid):
        mode = os.stat(path).st_mode
        xattr_set(f'{RSYNC_PREFIX}.%stat', f"{mode:o} 0,0 {uid}:{gid}", path)
        return True
else:
    rsync.RSYNC = rsync.RSYNC + ' --super'

    my_uid = os.getuid()
    if my_uid != 0:
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
    test_skipped("Can't chown (probably need root)", capability='ownership')
if not chown_or_fake(name2, 5001, 5003):
    test_skipped("Can't chown (probably need root)", capability='ownership')

os.chdir(FROMDIR.parent)
checkit(['-aHvv', 'from/', 'to/'], FROMDIR, TODIR)

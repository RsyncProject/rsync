#!/usr/bin/env python3
# Functional regression: a relative --link-dest=../sibling against a daemon
# module with `path = /` (the intersection of #897 and #915).
#
# #915 re-anchors the receiver's basis open at the module root so an in-module
# "../01" climb is honoured.  The gate keyed on a nonzero module_dirlen, but a
# `path = /` module has module_dirlen == 0 (clientserver.c), so the re-anchor
# was skipped there and --link-dest=../01 was silently ignored (every file
# re-transferred) even though plain #915 modules were fixed.
#
# Like link-dest-relative-basis, the secure per-component walk now honours the
# in-module `..` climb on every platform (it pops `..` to the held parent), so a
# `path = /` module uses the relative basis too.  Runs at any uid.

import shutil
import subprocess

from rsyncfns import (
    SCRATCHDIR, make_data_file, makepath, rmtree, rsync_argv, start_test_daemon,
    test_fail, write_daemon_conf,
)

DAEMON_PORT = 12931
DATA_SIZE = 40000

# dest 00 and basis 01 live side by side under `base`; the module is rooted at
# "/", so the served subtree is addressed by its absolute path minus the leading
# slash, and --link-dest=../01 climbs dest 00 -> sibling 01 (both inside /).
base = SCRATCHDIR / 'bakroot'
src = SCRATCHDIR / 'srcroot'
rmtree(base)
rmtree(src)
makepath(base / '01', src)
make_data_file(src / 'f.dat', DATA_SIZE)
shutil.copy2(src / 'f.dat', base / '01' / 'f.dat')

conf = write_daemon_conf([
    ('root', {'path': '/', 'read only': 'no'}),
])
url = start_test_daemon(conf, DAEMON_PORT)

base_rel = str(base).lstrip('/')          # address `base` via the path=/ module
rmtree(base / '00')
proc = subprocess.run(
    rsync_argv('-a', '--link-dest=../01', f'{src}/', f'{url}root/{base_rel}/00/'),
    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
out = proc.stdout or ''
if proc.returncode != 0:
    test_fail(f"path=/ --link-dest push failed unexpectedly (rc={proc.returncode}):\n{out}")

dest = base / '00' / 'f.dat'
basis = base / '01' / 'f.dat'
if not dest.is_file():
    test_fail(f"destination file missing ({dest})")

ds, bs = dest.stat(), basis.stat()
if (ds.st_dev, ds.st_ino) != (bs.st_dev, bs.st_ino):
    test_fail(
        "#915 (path=/ case): a `path = /` daemon module ignored --link-dest=../01 "
        "(module_dirlen==0 skipped the re-anchor) -- the file was re-transferred "
        "instead of hard-linked.  The secure walk must honour the in-module climb.")
# Honoured: the dest is hard-linked to the in-module sibling basis.

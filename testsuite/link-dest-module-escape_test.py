#!/usr/bin/env python3
# Security guard for the #915 re-anchor: a daemon receiver must NOT honour an
# alt-basis dir whose `..` climbs OUT of the module.
#
# Honouring a relative --link-dest=../01 again (#915) deliberately re-permits an
# in-module `..` climb (dest 00 -> sibling basis 01).  This test pins the other
# side of that boundary: a client-supplied --link-dest=../../OUTSIDE that points
# at a file OUTSIDE the module root must be refused, so the basis is never used
# and the dest file is re-transferred rather than hard-linked to the outside
# file (which would be an info-leak / cross-module hard-link).
#
# The re-anchor confines resolution beneath module_dir with RESOLVE_BENEATH, so
# the escaping climb is rejected in-kernel; on platforms without
# openat2/O_RESOLVE_BENEATH the portable resolver rejects the `..` outright.
# Either way the escape is blocked, so this test must PASS on every platform.
# Runs at any uid.

import shutil
import subprocess

from rsyncfns import (
    SCRATCHDIR, make_data_file, makepath, rmtree, rsync_argv, start_test_daemon,
    test_fail, write_daemon_conf,
)

DAEMON_PORT = 12916
DATA_SIZE = 40000

mod = SCRATCHDIR / 'escmod'          # daemon module root (holds dest 00)
src = SCRATCHDIR / 'escsrc'
outside = SCRATCHDIR / 'OUTSIDE'     # sibling of the module root -- OUTSIDE it
for d in (mod, src, outside):
    rmtree(d)
makepath(mod / '00', src, outside)

# Source file, plus a byte-identical secret OUTSIDE the module with the same
# name/size/mtime (so a followed basis would quick-check as a match).
make_data_file(src / 'f.dat', DATA_SIZE)
shutil.copy2(src / 'f.dat', outside / 'f.dat')

conf = write_daemon_conf([
    ('bak', {'path': str(mod), 'read only': 'no'}),
])
url = start_test_daemon(conf, DAEMON_PORT)

# Dest is bak/00 (cwd = module/00).  --link-dest=../../OUTSIDE climbs
# module/00 -> module -> SCRATCHDIR/OUTSIDE, i.e. out of the module.
proc = subprocess.run(
    rsync_argv('-a', '--link-dest=../../OUTSIDE', f'{src}/', f'{url}bak/00/'),
    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
out = proc.stdout or ''
if proc.returncode not in (0, 23):    # 23: a basis rejection is non-fatal here
    test_fail(f"escape push failed unexpectedly (rc={proc.returncode}):\n{out}")

dest = mod / '00' / 'f.dat'
secret = outside / 'f.dat'
if not dest.is_file():
    test_fail(f"destination file missing ({dest})")

ds, ss = dest.stat(), secret.stat()
if (ds.st_dev, ds.st_ino) == (ss.st_dev, ss.st_ino):
    test_fail(
        "MODULE ESCAPE: the dest was hard-linked to a file OUTSIDE the module "
        f"via --link-dest=../../OUTSIDE -- the confined resolver let a `..` "
        f"climb escape the module root.\n{out}")
# Escape blocked: the basis was refused, so the file was re-transferred and the
# dest is its own inode, not the outside secret's.

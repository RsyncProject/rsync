#!/usr/bin/env python3

import shutil
import subprocess

from harness.rsync import (
    SCRATCHDIR, make_data_file, makepath, rmtree, rsync_argv, start_test_daemon,
    test_fail, write_daemon_conf,
)

DAEMON_PORT = 12931
DATA_SIZE = 40000

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

base_rel = str(base).lstrip('/')
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

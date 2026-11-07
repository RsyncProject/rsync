#!/usr/bin/env python3

import os
import shutil
import subprocess

from harness.daemon_config import write_daemon_conf
from harness.rsync import (
    SCRATCHDIR, make_data_file, makepath, rmtree, rsync_argv, start_test_daemon, test_fail,
)

DAEMON_PORT = 12916
DATA_SIZE = 40000

mod = SCRATCHDIR / 'escmod'
src = SCRATCHDIR / 'escsrc'
copy_src = SCRATCHDIR / 'copy-src'
outside = SCRATCHDIR / 'OUTSIDE'
for d in (mod, src, copy_src, outside):
    rmtree(d)
makepath(mod / '00', src, copy_src, outside)

make_data_file(src / 'f.dat', DATA_SIZE)
shutil.copy2(src / 'f.dat', outside / 'f.dat')
(copy_src / 'copy.dat').write_text('SOURCE-CONTENT\n')
(outside / 'copy.dat').write_text('SECRET-CONTENT\n')
timestamp = 1_234_567_890
for path in (copy_src / 'copy.dat', outside / 'copy.dat'):
    path.chmod(0o644)
    os.utime(path, (timestamp, timestamp))
(mod / 'copy-basis').symlink_to(outside, target_is_directory=True)

conf = write_daemon_conf([
    ('bak', {'path': str(mod), 'read only': 'no'}),
])
url = start_test_daemon(conf, DAEMON_PORT)

proc = subprocess.run(
    rsync_argv('-a', '--link-dest=../../OUTSIDE', f'{src}/', f'{url}bak/00/'),
    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
out = proc.stdout or ''
if proc.returncode not in (0, 23):
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

proc = subprocess.run(
    rsync_argv('-rtp', '--copy-dest=copy-basis', f'{copy_src}/', f'{url}bak/'),
    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
copied = mod / 'copy.dat'
if proc.returncode not in (0, 23) or not copied.is_file():
    test_fail(f'confined --copy-dest transfer failed: {proc.stdout}')
if copied.read_text() != 'SOURCE-CONTENT\n':
    test_fail('--copy-dest read outside module through a trusted symlink')

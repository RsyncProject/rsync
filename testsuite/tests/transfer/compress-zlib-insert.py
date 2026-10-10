#!/usr/bin/env python3

import filecmp
import shutil
import subprocess

from harness.daemon_config import write_daemon_conf
from harness.rsync import (
    SCRATCHDIR, make_data_file, makepath, rmtree, rsync_argv, start_test_daemon, test_fail,
)

DAEMON_PORT = 12922
SIZE = 8 * 1024 * 1024
BLOCK = 65535

moddir = SCRATCHDIR / 'zmod'
srcdir = SCRATCHDIR / 'zsrc'
rmtree(moddir)
rmtree(srcdir)
makepath(moddir)
makepath(srcdir)

make_data_file(srcdir / 'big.dat', SIZE)
shutil.copy(srcdir / 'big.dat', moddir / 'big.dat')
with open(srcdir / 'big.dat', 'r+b') as f:
    f.seek(SIZE // 2 + 1000)
    f.write(b'\x00' * 32)

conf = write_daemon_conf([('zmod', {'path': str(moddir), 'read only': 'no'})])
url = start_test_daemon(conf, DAEMON_PORT) + 'zmod/'

proc = subprocess.run(
    rsync_argv('-zI', '--compress-choice=zlib', '--no-whole-file',
               f'--block-size={BLOCK}', str(srcdir / 'big.dat'), url),
    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
if proc.returncode != 0:
    print(proc.stdout)
    test_fail(f"zlib delta upload failed (rc={proc.returncode}); "
              "regression of #951 deflate-token overflow")

if not filecmp.cmp(srcdir / 'big.dat', moddir / 'big.dat', shallow=False):
    test_fail("uploaded file differs from source -- zlib delta corruption")

#!/usr/bin/env python3

import os
import platform
import subprocess
import time

from harness.mutation import race_budget, start_path_flipper, stop_flipper
from harness.daemon_config import write_daemon_conf
from harness.rsync import SCRATCHDIR, rmtree, rsync_argv, start_test_daemon, test_fail, test_xfail

_CYGWIN = platform.system().startswith('CYGWIN')

base = SCRATCHDIR / 'sender-remove-source'
mod = base / 'module'
outside = base / 'outside'
dest = base / 'dest'
rmtree(base)
(mod / 'real').mkdir(parents=True)
outside.mkdir(parents=True)
dest.mkdir(parents=True)

inside_file = mod / 'real' / 'file'
outside_file = outside / 'file'
inside_file.write_text('payload\n')
outside_file.write_text('payload\n')
st = inside_file.stat()
os.utime(outside_file, (st.st_atime, st.st_mtime))
os.symlink(outside, mod / 'evil')

conf = write_daemon_conf([
    ('src', {'path': str(mod), 'read only': 'no', 'use chroot': 'no'}),
])
url = start_test_daemon(conf, 12937)

flip = start_path_flipper(mod / 'real', mod / 'evil')
deadline = time.monotonic() + race_budget()
try:
    while time.monotonic() < deadline and outside_file.exists():
        if not inside_file.exists() and (mod / 'real').is_dir() and not os.path.islink(mod / 'real'):
            try:
                inside_file.write_text('payload\n')
                os.utime(inside_file, (st.st_atime, st.st_mtime))
            except FileNotFoundError:
                pass
            except PermissionError:
                if not _CYGWIN:
                    raise
        subprocess.run(
            rsync_argv('-a', '--remove-source-files', f'{url}src/real/file', str(dest) + '/'),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, text=True)
finally:
    stop_flipper(flip)

if not outside_file.exists():
    if _CYGWIN:
        test_xfail("cygwin: --remove-source-files parent-flip race still unlinks "
                   "the outside victim -- documented Cygwin platform residual")
    test_fail("daemon sender --remove-source-files cleanup unlinked the outside victim through a raced parent symlink")

print("sender-remove-source-secure: remove-source cleanup did not unlink outside the module")

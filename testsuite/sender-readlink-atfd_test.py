#!/usr/bin/env python3
import os
import platform
import subprocess
import time

from rsyncfns import (
    race_budget, SCRATCHDIR, rmtree, rsync_argv,
    start_path_flipper, start_test_daemon, stop_flipper, test_fail, test_xfail,
    write_daemon_conf,
)

_CYGWIN = platform.system().startswith('CYGWIN')

# The sender's secure scan-dir open resolves on held dirfds with O_NOFOLLOW
# (race-free by construction on every platform), so a flipped parent component
# cannot redirect the readlink outside the module.

base = SCRATCHDIR / 'sender-readlink'
mod = base / 'module'
outside = base / 'outside'
dest = base / 'dest'
rmtree(base)
(mod / 'real').mkdir(parents=True)
outside.mkdir(parents=True)
dest.mkdir(parents=True)

os.symlink('inside-target', mod / 'real' / 'link')
os.symlink('OUTSIDE-LINK-TARGET', outside / 'link')
os.symlink(outside, mod / 'evil')

conf = write_daemon_conf([
    ('src', {'path': str(mod), 'read only': 'yes', 'use chroot': 'no'}),
])
url = start_test_daemon(conf, 12938)

flip = start_path_flipper(mod / 'real', mod / 'evil')
leaked = False
deadline = time.monotonic() + race_budget()
try:
    while time.monotonic() < deadline:
        rmtree(dest)
        dest.mkdir(parents=True)
        subprocess.run(
            rsync_argv('-a', f'{url}src/real/link', str(dest) + '/'),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, text=True)
        link = dest / 'link'
        if os.path.islink(link) and os.readlink(link) == 'OUTSIDE-LINK-TARGET':
            leaked = True
            break
finally:
    stop_flipper(flip)

if leaked:
    if _CYGWIN:
        test_xfail("cygwin: sender readlink parent-flip race still returns the "
                   "outside symlink target -- documented Cygwin platform residual")
    test_fail("daemon sender readlink followed a raced parent symlink and sent the outside symlink target")

print("sender-readlink-atfd: symlink target reads did not leak through a raced parent")

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

# The sender's secure parent-dir open resolves on held dirfds with O_NOFOLLOW
# (race-free by construction on every platform), so a flipped parent component
# cannot redirect the --remove-source-files unlink outside the module.

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
        subprocess.run(
            rsync_argv('-a', '--remove-source-files', f'{url}src/real/file', str(dest) + '/'),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, text=True)
finally:
    stop_flipper(flip)

if not outside_file.exists():
    if _CYGWIN:
        # Cygwin resolves directory descriptors by path name rather than a pinned
        # inode and emulates symlinks as special files, so the confined sender's
        # held-fd O_NOFOLLOW walk -- though compiled in -- does not enforce the
        # parent-component pin here, and a raced parent flip can still redirect
        # the --remove-source-files unlink. Documented Cygwin platform residual
        # (see SECURITY.md and the matching xfail in symlink-race-source_test.py);
        # Cygwin is a dev/interop target, not a privilege boundary host.
        test_xfail("cygwin: --remove-source-files parent-flip race still unlinks "
                   "the outside victim -- documented Cygwin platform residual")
    test_fail("daemon sender --remove-source-files cleanup unlinked the outside victim through a raced parent symlink")

print("sender-remove-source-secure: remove-source cleanup did not unlink outside the module")

#!/usr/bin/env python3
import socket
import stat

from rsyncfns import (
    SCRATCHDIR, makepath, rmtree, rsync_argv, start_test_daemon, test_skipped, test_fail,
    write_daemon_conf,
)
import subprocess

src = SCRATCHDIR / 'sock-src'
dst = SCRATCHDIR / 'sock-dst'
rmtree(src)
rmtree(dst)
makepath(src, dst)
sock_path = src / 's'
s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
try:
    s.bind(str(sock_path))
except OSError as e:
    test_skipped(f"cannot create unix-domain socket fixture: {e}")
finally:
    s.close()

conf = write_daemon_conf([
    ('sock', {'path': str(dst), 'read only': 'no', 'use chroot': 'no'}),
])
url = start_test_daemon(conf, 12935)
proc = subprocess.run(
    rsync_argv('-a', '--specials', f'{src}/', f'{url}sock/'),
    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
if proc.returncode != 0:
    test_fail(f"daemon socket-special upload failed:\n{proc.stderr}")
if not stat.S_ISSOCK((dst / 's').lstat().st_mode):
    test_fail("rsync did not preserve the unix-domain socket fixture")
print("daemon-unix-socket-atfd: daemon receiver preserves unix sockets without escaping parent confinement")

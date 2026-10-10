#!/usr/bin/env python3

import atexit
import os
import platform
import signal
import socket
import subprocess
import time

from harness.daemon_config import write_daemon_conf
from harness.rsync import (
    FROMDIR, RSYNC_PEER, SCRATCHDIR, claim_ports, make_tree, makepath, require_tcp, rmtree,
    rsync_argv, split_rsync_cmd, test_fail, test_skipped,
)

if platform.system().startswith('CYGWIN'):
    test_skipped("a detached daemon orphans on cygwin's Windows process model",
                 capability='posix_detach')

PORT = 19877
require_tcp("standalone detaching daemon opens a real loopback listener; "
            "run with --use-tcp")
claim_ports(PORT)

src = FROMDIR
rmtree(src)
make_tree(src, depth=2)
dest = SCRATCHDIR / 'dest-detach'
makepath(dest)

pidfile = SCRATCHDIR / 'detach.pid'
logfile = SCRATCHDIR / 'detach.log'
for p in (pidfile, logfile):
    if p.exists():
        p.unlink()

conf = write_daemon_conf(
    [('mod', {'path': str(dest), 'read only': 'no', 'use chroot': 'no'})],
    global_options={
        'port': str(PORT),
        'address': '127.0.0.1',
        'pid file': str(pidfile),
        'log file': str(logfile),
    },
    name='detach.conf',
)

launcher = subprocess.run(
    split_rsync_cmd(RSYNC_PEER) + ['--daemon', f'--config={conf}'],
    capture_output=True, text=True, timeout=15,
)
if launcher.returncode != 0:
    test_fail(f"daemon launcher exited {launcher.returncode}:\n{launcher.stderr}")

def kill_detached():
    try:
        pid = int(pidfile.read_text().strip())
    except (FileNotFoundError, ValueError):
        return
    try:
        os.kill(pid, signal.SIGTERM)
        for _ in range(50):
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return
            time.sleep(0.05)
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass

atexit.register(kill_detached)

deadline = time.monotonic() + 10
while time.monotonic() < deadline:
    if pidfile.is_file():
        try:
            with socket.create_connection(('127.0.0.1', PORT), timeout=0.5):
                break
        except OSError:
            pass
    time.sleep(0.05)
else:
    test_fail(f"detached rsyncd never listened on 127.0.0.1:{PORT} "
              f"(pidfile={'present' if pidfile.is_file() else 'absent'}; "
              f"log:\n{logfile.read_text() if logfile.is_file() else '(none)'})")

log = logfile.read_text()
if f'listening on port {PORT}' not in log:
    test_fail(f"expected 'listening on port {PORT}' in detach.log:\n{log}")

r = subprocess.run(
    rsync_argv('-r', f'{src}/', f'rsync://127.0.0.1:{PORT}/mod/'),
    capture_output=True, text=True,
)
if r.returncode != 0:
    test_fail(f"push to detached daemon failed (rc={r.returncode}):\n{r.stderr}")
if not any(dest.iterdir()):
    test_fail("push to detached daemon wrote nothing")

detached_pid = pidfile.read_text().strip()
kill_detached()
atexit.unregister(kill_detached)

print(f"daemon-standalone-detach: become_daemon + conf port/address + "
      f"accept-loop transfer ok (pid {detached_pid})")

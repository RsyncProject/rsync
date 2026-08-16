#!/usr/bin/env python3
"""Daemon coverage: standalone detaching listener.

Every other daemon test spawns rsyncd with `--no-detach --address=127.0.0.1
--port=N`, so daemon_main()'s detach path and the rsyncd.conf port/address
parameters are never reached:

  clientserver.c become_daemon()   -- only when --no-detach is NOT given
  daemon-parm.h  lp_rsync_port()   -- only when --port is NOT given
  daemon-parm.h  lp_bind_address() -- only when --address is NOT given

This test spawns `rsync --daemon --config=<conf>` with NONE of those flags,
reads port/address from the conf file, waits for the detached child to write
its pid file and start listening, does one transfer, then kills it via the
pid file.  require_tcp-gated (it opens a real loopback listener).
"""

import os
import platform
import shlex
import signal
import socket
import subprocess
import time

from rsyncfns import (
    FROMDIR, RSYNC_PEER, SCRATCHDIR,
    claim_ports, make_tree, makepath, require_tcp, rmtree, rsync_argv,
    test_fail, test_skipped, write_daemon_conf, split_rsync_cmd,
)

# A standalone --daemon detaches via become_daemon(); on cygwin that becomes a
# disconnected Windows process the harness can't reap, so it lingers as an orphan
# squatting its port and poisoning later TCP daemon tests.  Skip on cygwin.
if platform.system().startswith('CYGWIN'):
    test_skipped("a detached daemon orphans on cygwin's Windows process model")

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
    globals={
        # These two are the point of the test: read from the conf, not CLI.
        'port': str(PORT),
        'address': '127.0.0.1',
        'pid file': str(pidfile),
        'log file': str(logfile),
    },
    name='detach.conf',
)

# Spawn WITHOUT --no-detach / --port / --address.  The launched process is
# become_daemon()'s PARENT: it forks, gcov_flush()es, and _exit(0)s
# immediately; the detached CHILD writes the pid file and listens.
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


import atexit
atexit.register(kill_detached)

# Wait for the detached child: pid file written AND port accepting.
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

# Verify the log shows the conf-supplied port (lp_rsync_port path).
log = logfile.read_text()
if f'listening on port {PORT}' not in log:
    test_fail(f"expected 'listening on port {PORT}' in detach.log:\n{log}")

# One real transfer through the per-connection accept-loop child.
r = subprocess.run(
    rsync_argv('-r', f'{src}/', f'rsync://127.0.0.1:{PORT}/mod/'),
    capture_output=True, text=True,
)
if r.returncode != 0:
    test_fail(f"push to detached daemon failed (rc={r.returncode}):\n{r.stderr}")
if not any(dest.iterdir()):
    test_fail("push to detached daemon wrote nothing")

# Clean shutdown via SIGTERM -> exit_cleanup -> gcov_flush in the
# called_from_signal_handler path (cleanup.c).  The daemon removes its own
# pid file on the way out, so capture it first.
detached_pid = pidfile.read_text().strip()
kill_detached()
atexit.unregister(kill_detached)

print(f"daemon-standalone-detach: become_daemon + conf port/address + "
      f"accept-loop transfer ok (pid {detached_pid})")

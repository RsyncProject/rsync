#!/usr/bin/env python3
# Python rewrite of testsuite/proxy-response-line-too-long.test.
#
# Regression test for the off-by-one stack OOB write in
# establish_proxy_connection() when a malicious or man-in-the-middle
# HTTP proxy returned a first response line of 1023+ bytes without a
# '\n' terminator. Post-fix, rsync must reject this with "proxy
# response line too long" and exit non-zero without dying from a signal.

import os
import shutil
import socket
import subprocess
import sys
import threading
import time

from rsyncfns import (
    SCRATCHDIR, claim_ports, require_tcp, rsync_argv, test_fail, test_skipped,
)


# This test has no stdio-pipe equivalent: it binds a real loopback socket to
# stand in for a malicious HTTP proxy. Honour the secure default (no listening
# sockets) by only running it under --use-tcp.
require_tcp("fake-proxy listener needs a real TCP socket; run with --use-tcp")

if shutil.which('python3') is None:
    test_skipped("python3 not available")

workdir = SCRATCHDIR / 'workdir'
workdir.mkdir(parents=True, exist_ok=True)
os.chdir(workdir)

# Reserve our proxy port across any concurrent test processes; another
# test asking for the same port blocks here until we exit.
PROXY_PORT = 12873
claim_ports(PROXY_PORT)

# In-process listener: bind to the claimed port, accept one client, read
# up to end-of-headers (or 64 KiB), reply with exactly 1023 'X' bytes and
# no '\n', then close. A thread is simpler than spawning python3 again
# and has the same effect on the rsync side.
listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
listener.bind(('127.0.0.1', PROXY_PORT))
port = PROXY_PORT
listener.listen(1)


def _serve_one():
    conn, _ = listener.accept()
    conn.settimeout(5)
    try:
        data = b""
        while b"\r\n\r\n" not in data and len(data) < 65536:
            chunk = conn.recv(8192)
            if not chunk:
                break
            data += chunk
    except socket.timeout:
        pass
    conn.sendall(b"X" * 1023)
    try:
        conn.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass
    conn.close()


t = threading.Thread(target=_serve_one)
t.daemon = True
t.start()

# Run rsync against the malicious proxy. The proxy intercepts CONNECT
# and never forwards, so the upstream URL is irrelevant.
env = os.environ.copy()
env['RSYNC_PROXY'] = f'127.0.0.1:{port}'
proc = subprocess.run(
    rsync_argv('rsync://example.invalid:873/whatever/', f'{workdir}/out/'),
    capture_output=True, text=True, env=env,
)

t.join(timeout=15)
listener.close()

status = proc.returncode
err = proc.stderr

if status >= 128:
    sys.stderr.write(err)
    test_fail(f"rsync killed by signal (status={status}) -- possible stack OOB regression")

if status == 0:
    sys.stderr.write(err)
    test_fail("rsync returned success despite malformed proxy response")

if 'proxy response line too long' not in err:
    sys.stderr.write(err)
    test_fail("expected 'proxy response line too long' in rsync stderr")

print("OK: over-long proxy response line rejected cleanly without crashing")

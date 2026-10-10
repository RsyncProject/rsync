#!/usr/bin/env python3

import os
import socket
import subprocess

from harness.rsync import SCRATCHDIR, rmtree, rsync_argv, test_fail, test_skipped

base = SCRATCHDIR / 'proxy-crlf'
rmtree(base)
base.mkdir(parents=True)

try:
    lsock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    lsock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    lsock.bind(('127.0.0.1', 0))
    lsock.listen(1)
except OSError as e:
    test_skipped(f"cannot bind a localhost proxy listener: {e}")
proxy_port = lsock.getsockname()[1]

env = os.environ.copy()
env['RSYNC_PROXY'] = f'127.0.0.1:{proxy_port}'

marker = 'INJECTEDLINE'
host = f'evilhost\r\n{marker}'

proc = subprocess.Popen(
    rsync_argv(f'rsync://{host}/mod/', str(base / 'out')),
    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)

received = b''
try:
    lsock.settimeout(10)
    conn, _ = lsock.accept()
    conn.settimeout(5)
    try:
        while True:
            chunk = conn.recv(4096)
            if not chunk:
                break
            received += chunk
            if b'\r\n\r\n' in received or len(received) > 65536:
                break
    except socket.timeout:
        pass
    conn.close()
except socket.timeout:
    pass
finally:
    lsock.close()

out = proc.communicate(timeout=30)
out = (out[0] or '') + (out[1] or '')

if marker.encode() in received:
    test_fail("CRLF-injected marker reached the proxy CONNECT stream:\n"
              + repr(received))
if proc.returncode == 0:
    test_fail("malicious proxy host unexpectedly produced a successful transfer")

print("proxy-host-crlf: control characters in the daemon host are rejected")

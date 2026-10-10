#!/usr/bin/env python3

import socket
import subprocess
import threading

from harness.rsync import (
    SCRATCHDIR, claim_ports, makepath, require_tcp, rmtree, rsync_argv, test_fail,
)
from harness import protocol as rp

PORT = 12972
require_tcp("the pure-Python receiver needs a real TCP socket; run with --use-tcp")
claim_ports(PORT)

base = SCRATCHDIR / 'checksum-zero-blocklen'
rmtree(base)
src = base / 'src'
makepath(src)
(src / 'f').write_text('new data\n' * 4096)

lsock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
lsock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
lsock.bind(('127.0.0.1', PORT))
lsock.listen(1)
lsock.settimeout(30)

state = {}

def serve():
    try:
        csock, _ = lsock.accept()
        r = rp.DaemonReceiver(csock)
        r.handshake()
        r.send_sum_request(0, count=1, blength=0, s2length=0, remainder=0)
        r.drain()
        r.close()
    except Exception as exc:
        state['err'] = repr(exc)

t = threading.Thread(target=serve, daemon=True)
t.start()
try:
    proc = subprocess.run(
        rsync_argv('--no-whole-file', f'{src}/f', f'rsync://127.0.0.1:{PORT}/mod/'),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30)
finally:
    t.join(timeout=10)
    lsock.close()

out = (proc.stdout or '') + (proc.stderr or '')
if state.get('err'):
    test_fail(f"the Python receiver failed before/while feeding the sender: {state['err']}\n"
              f"sender output:\n{out}")
if proc.returncode == 0:
    test_fail("malicious receiver sent a count>0/blength=0 checksum header "
              f"but the sender completed successfully:\n{out}")
if 'Invalid zero block length' not in out:
    test_fail("sender rejected the malicious receiver, but not via the zero-block "
              f"checksum guard. Output:\n{out}")

print("checksum-zero-blocklen: sender rejects count>0 checksum headers with blength=0")

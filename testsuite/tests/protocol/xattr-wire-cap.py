#!/usr/bin/env python3

import socket
import subprocess
import threading

from harness.rsync import (
    SCRATCHDIR, claim_ports, forced_protocol, makepath, require_tcp, rmtree,
    rsync_argv, test_fail, test_skipped, xattrs_supported,
)
from harness import protocol as rp

if not xattrs_supported():
    test_skipped("xattr-wire-cap requires rsync and filesystem xattr support",
                 capability='xattr_wire')
proto = forced_protocol()
if proto is not None and proto < 30:
    test_skipped(f"xattr-wire-cap requires protocol 30+ (xattrs unsupported at negotiated {proto})",
                 capability='protocol_30')

PORT = 12970
require_tcp("the pure-Python daemon needs a real TCP socket; run with --use-tcp")
claim_ports(PORT)

base = SCRATCHDIR / 'xattr-wire-cap'
rmtree(base)
dest = base / 'dest'
makepath(dest)

xattr = rp.xattr_list_wire([(b'user.wirecap\0', 128 * 1024 * 1024 + 1, b'')])

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
        flist = (rp.FileEntry('f', mode=rp.S_IFREG | 0o644, length=8).encode()
                 + xattr + rp.end_of_flist(0, r.protocol))
        r.send_data(flist)
        r.drain()
        r.close()
    except Exception as exc:
        state['err'] = repr(exc)

t = threading.Thread(target=serve, daemon=True)
t.start()
try:
    proc = subprocess.run(
        rsync_argv('-aX', f'rsync://127.0.0.1:{PORT}/x/f', str(dest) + '/'),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30)
finally:
    t.join(timeout=10)
    lsock.close()

out = (proc.stdout or '') + (proc.stderr or '')
if state.get('err'):
    test_fail(f"the Python daemon failed before/while streaming the file list: {state['err']}\n"
              f"receiver output:\n{out}")
if proc.returncode == 0:
    test_fail("malicious daemon sender advertised an oversized xattr datum length "
              f"but the receiver completed successfully:\n{out}")
if 'xattr datum_len exceeds per-value limit' not in out:
    test_fail("receiver rejected the malicious xattr stream, but not via the per-value "
              f"wire cap. Output:\n{out}")

print("xattr-wire-cap: receiver rejects oversized peer-supplied xattr datum lengths")

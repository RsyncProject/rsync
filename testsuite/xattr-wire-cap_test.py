#!/usr/bin/env python3
# A malicious DAEMON acting as the SENDER can put an oversized per-value xattr
# datum_len in the file list it streams to a pulling client.  receive_xattr()
# (xattrs.c) guards this: read_varint_size() bounds it to MAX_WIRE_XATTR_DATALEN
# and a second check rejects anything over MAX_XATTR_VALUE_BYTES (128 MiB) with
# "xattr datum_len exceeds per-value limit", before the value is read.
#
# The malicious bytes flow daemon(sender) -> client(receiver) in the FILE-LIST
# phase (send_xattr() is appended to each entry under -X), so we drive it with
# the SERVER side of rsync_proto (DaemonReceiver acting as the sender): stand up
# a tiny Python "daemon" that a real rsync client PULLS from, run the handshake,
# and stream a one-entry file list whose xattr block declares datum_len =
# 128 MiB + 1.  receive_xattr() runs during recv_file_list(), so no transfer
# phase is needed, and because the receiver rejects at the datum_len check we
# needn't send the value itself.
#
# Oracle: the real receiver exits non-zero with "xattr datum_len exceeds
# per-value limit".  Needs xattr support and a real TCP socket (--use-tcp).

import socket
import subprocess
import threading

from rsyncfns import (
    SCRATCHDIR, claim_ports, forced_protocol, makepath, require_tcp, rmtree,
    rsync_argv, test_fail, test_skipped, xattrs_supported,
)
import rsync_proto as rp

if not xattrs_supported():
    test_skipped("xattr-wire-cap requires rsync and filesystem xattr support")
proto = forced_protocol()
if proto is not None and proto < 30:
    test_skipped(f"xattr-wire-cap requires protocol 30+ (xattrs unsupported at negotiated {proto})")

PORT = 12970
require_tcp("the pure-Python daemon needs a real TCP socket; run with --use-tcp")
claim_ports(PORT)

base = SCRATCHDIR / 'xattr-wire-cap'
rmtree(base)
dest = base / 'dest'
makepath(dest)

# send_xattr() bytes for a new list with one xattr whose declared value length
# is 128 MiB + 1 -- it clears the wire cap (read_varint_size, < 2 GiB) but trips
# the per-value cap (MAX_XATTR_VALUE_BYTES = 128 MiB).  No value follows: the
# receiver rejects before reading it.
xattr = rp.xattr_list_wire([(b'user.wirecap\0', 128 * 1024 * 1024 + 1, b'')])

lsock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
lsock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
lsock.bind(('127.0.0.1', PORT))
lsock.listen(1)
lsock.settimeout(30)

state = {}


def serve():
    """Accept the pulling client and, as the sender, stream a one-entry file
    list whose xattr block carries the oversized datum length."""
    try:
        csock, _ = lsock.accept()
        r = rp.DaemonReceiver(csock)
        r.handshake()
        flist = (rp.FileEntry('f', mode=rp.S_IFREG | 0o644, length=8).encode()
                 + xattr + rp.end_of_flist(0, r.protocol))
        r.send_data(flist)
        r.drain()
        r.close()
    except Exception as exc:                       # noqa: BLE001 - reported below
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

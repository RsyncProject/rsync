#!/usr/bin/env python3
import socket

from rsyncfns import (
    SCRATCHDIR, claim_ports, require_tcp, start_rsyncd, test_fail,
)

PORT = 12933
require_tcp("PROXY protocol peer policy needs a real TCP daemon; run with --use-tcp")
claim_ports(PORT)

mod = SCRATCHDIR / 'proxy-proto-mod'
mod.mkdir(parents=True, exist_ok=True)
conf = SCRATCHDIR / 'proxy-proto.conf'
log = SCRATCHDIR / 'proxy-proto.log'
conf.write_text(f"""\
pid file = {SCRATCHDIR}/rsyncd.pid
proxy protocol = true
use chroot = no
log file = {log}

[mod]
    path = {mod}
    read only = yes
""")
start_rsyncd(conf, PORT)

with socket.create_connection(('127.0.0.1', PORT), timeout=5) as s:
    s.sendall(b"PROXY TCP4 10.9.8.7 127.0.0.1 12345 873\r\n")
    try:
        data = s.recv(1024)
    except ConnectionResetError:
        data = b''

if b'@RSYNCD' in data:
    test_fail("daemon trusted a PROXY header from an unlisted direct peer")
log_text = log.read_text(errors='replace')
if 'proxy protocol rejected' not in log_text:
    test_fail("daemon did not log rejection of untrusted PROXY protocol peer")
# This config sets "proxy protocol = true" with no "proxy protocol hosts", which
# fail-closes (rejects every peer); the daemon must warn about that at startup.
if 'proxy protocol hosts" is unset' not in log_text:
    test_fail("daemon did not warn at startup that proxy-protocol with no "
              "trusted-proxy hosts rejects all connections")

print("proxy-protocol-trusted-peer: untrusted direct peer cannot spoof PROXY source IP")

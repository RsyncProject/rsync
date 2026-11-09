#!/usr/bin/env python3
import socket

from rsyncfns import SCRATCHDIR, claim_ports, require_tcp, start_rsyncd, test_fail

PORT = 12934
require_tcp("raw malicious daemon client needs a real TCP daemon; run with --use-tcp")
claim_ports(PORT)

mod = SCRATCHDIR / 'argv-mod'
mod.mkdir(parents=True, exist_ok=True)
conf = SCRATCHDIR / 'argv-limit.conf'
log = SCRATCHDIR / 'argv-limit.log'
conf.write_text(f"""\
pid file = {SCRATCHDIR}/rsyncd.pid
use chroot = no
log file = {log}

[mod]
    path = {mod}
    read only = yes
""")
start_rsyncd(conf, PORT)

with socket.create_connection(('127.0.0.1', PORT), timeout=5) as s:
    greeting = s.recv(4096)
    if not greeting.startswith(b'@RSYNCD:'):
        test_fail(f"unexpected daemon greeting: {greeting!r}")
    s.sendall(b'@RSYNCD: 31.0\n')
    s.sendall(b'mod\n')
    ok = s.recv(4096)
    if b'@RSYNCD: OK' not in ok:
        test_fail(f"daemon did not accept test module: {ok!r}")

    payload = b''.join(b'a%05d\0' % i for i in range(17000)) + b'\0'
    s.sendall(payload)
    try:
        s.recv(4096)
    except (ConnectionResetError, BrokenPipeError):
        pass

text = log.read_text(errors='replace')
if 'too many daemon arguments' not in text:
    test_fail("daemon did not reject the malicious client's oversized argv list")

print("daemon-argv-limit: malicious client argv flood is rejected")

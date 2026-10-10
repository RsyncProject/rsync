#!/usr/bin/env python3

import time

from harness.rsync import (
    SCRATCHDIR, claim_ports, require_tcp, start_rsyncd, test_fail,
)
from harness import protocol as rp

PORT = 12955
MARKER = b'SCANNER-0009-FORWARDED'
require_tcp("the pure-Python sender needs a real TCP daemon; run with --use-tcp")
claim_ports(PORT)

mod = SCRATCHDIR / 'mi-mod'
mod.mkdir(parents=True, exist_ok=True)
conf = SCRATCHDIR / 'mi.conf'
log = SCRATCHDIR / 'mi.log'
conf.write_text(f"""\
pid file = {SCRATCHDIR}/mi-rsyncd.pid
use chroot = no
log file = {log}

[mod]
    path = {mod}
    read only = no
""")
start_rsyncd(conf, PORT)

s = rp.DaemonSender('127.0.0.1', PORT)
s.handshake('mod', ['--server', '-e.LsfxCIu', '.', 'mod/'], greeting_version=30)
s.send_flat_flist([rp.FileEntry('hello.txt', mode=rp.S_IFREG | 0o644, length=0)])
s.send_message(rp.MSG_INFO, MARKER + b'\n')
back = s.drain(timeout=3.0)
s.close()

forwarded = MARKER in back
for _ in range(50):
    if log.exists() and MARKER.decode() in log.read_text(errors='replace'):
        forwarded = True
        break
    if forwarded:
        break
    time.sleep(0.1)

if not forwarded:
    log_text = log.read_text(errors='replace') if log.exists() else '(no log)'
    test_fail(
        "daemon receiver did not forward a peer MSG_INFO -- it aborted on the "
        "rwrite() assert(!is_utf8) (the marker never reached the generator).\n"
        f"daemon log:\n{log_text}")

print("msg-info-forwarding: daemon receiver forwarded a peer MSG_INFO without "
      "aborting (the rwrite assert is gone).")

#!/usr/bin/env python3

import time

from harness.rsync import (
    SCRATCHDIR, claim_ports, require_tcp, start_rsyncd, test_fail,
)
from harness import protocol as rp

PORT = 12944
require_tcp("the pure-Python sender needs a real TCP daemon; run with --use-tcp")
claim_ports(PORT)

mod = SCRATCHDIR / 'proto-mod'
mod.mkdir(parents=True, exist_ok=True)
conf = SCRATCHDIR / 'proto-selftest.conf'
log = SCRATCHDIR / 'proto-selftest.log'
conf.write_text(f"""\
pid file = {SCRATCHDIR}/proto-rsyncd.pid
use chroot = no
log file = {log}

[mod]
    path = {mod}
    read only = no
""")
start_rsyncd(conf, PORT)

SERVER_ARGS = ['--server', '-e.LsfxCIu', '.', 'mod/']

def push(raw_entries):
    before = log.read_text(errors='replace') if log.exists() else ''
    s = rp.DaemonSender('127.0.0.1', PORT)
    s.handshake('mod', SERVER_ARGS, greeting_version=30)
    buf = bytearray()
    for e in raw_entries:
        buf += e
    buf += rp.end_of_flist(0, s.protocol)
    s.send_data(bytes(buf))
    s.drain(timeout=2.0)
    s.close()
    for _ in range(50):
        text = log.read_text(errors='replace')
        if len(text) > len(before):
            break
        time.sleep(0.1)
    return text[len(before):]

good = rp.FileEntry('hello.txt', mode=rp.S_IFREG | 0o644, length=0).encode()
good_log = push([good])
if 'receiving file list' not in good_log:
    test_fail("daemon never reached 'receiving file list' for a well-formed "
              f"push -- handshake/setup encoding is wrong.\nlog:\n{good_log}")
if 'overflow' in good_log:
    test_fail("a well-formed entry tripped the recv_file_entry overflow guard; "
              f"the encoder is mis-aligned.\nlog:\n{good_log}")

bad = (rp.w_byte(rp.XMIT_LONG_NAME | rp.XMIT_SAME_UID | rp.XMIT_SAME_GID)
       + rp.w_varint(0x7fffff) + b'xx')
bad_log = push([bad])
if 'overflow' not in bad_log:
    test_fail("daemon did NOT hit recv_file_entry's name-length overflow guard "
              "for an XMIT_LONG_NAME entry with a huge varint length; the flag "
              f"or varint encoding is wrong (the parser never saw it).\n"
              f"log:\n{bad_log}")

print("protocol-sender: daemon's recv_file_entry parses the protocol helper's "
      "flags + varint name-length exactly (well-formed accepted, "
      "XMIT_LONG_NAME overflow rejected).")

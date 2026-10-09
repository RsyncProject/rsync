#!/usr/bin/env python3
# Self-test / bitrot guard for the pure-Python rsync sender (rsync_proto.py).
#
# rsync_proto.py is a second, hand-written implementation of the rsync
# client/sender wire format, used to push (possibly malformed) file lists to a
# real daemon receiver for security regression tests without recompiling a
# patched rsync. Because it duplicates io.c / flist.c / compat.c on-wire
# behaviour, it can silently drift when the protocol changes. This test pins it
# against the rsync binary under test by checking that the daemon's
# recv_file_entry() actually parses the bytes we encode:
#
#   * the @RSYNCD handshake + protocol-30 setup completes (no exception), and
#   * a well-formed flat file list is parsed without any flist error, and
#   * an entry whose name length is encoded as XMIT_LONG_NAME + an absurd
#     varint trips recv_file_entry()'s "overflow: xflags=..." guard
#     (flist.c) -- which is only reachable if the daemon read our flags byte
#     and varint name-length exactly, proving the encoder is correct (a
#     desync would error elsewhere, or not at all).
#
# Needs a real TCP daemon (raw socket speaker), so it is skipped unless
# --use-tcp is in effect.

import time

from rsyncfns import (
    SCRATCHDIR, claim_ports, require_tcp, start_rsyncd, test_fail,
)
import rsync_proto as rp

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
    """Handshake, send a flat flist built from raw entry byte-strings, and
    return the daemon log text produced for this connection."""
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


# --- 1. a well-formed entry parses with no flist error ----------------------
good = rp.FileEntry('hello.txt', mode=rp.S_IFREG | 0o644, length=0).encode()
good_log = push([good])
if 'receiving file list' not in good_log:
    test_fail("daemon never reached 'receiving file list' for a well-formed "
              f"push -- handshake/setup encoding is wrong.\nlog:\n{good_log}")
if 'overflow' in good_log:
    test_fail("a well-formed entry tripped the recv_file_entry overflow guard; "
              f"the encoder is mis-aligned.\nlog:\n{good_log}")

# --- 2. XMIT_LONG_NAME + an absurd varint name length is parsed and rejected -
# flags byte 0x58 = XMIT_LONG_NAME|XMIT_SAME_UID|XMIT_SAME_GID (no high bits,
# no XMIT_EXTENDED_FLAGS -> single byte); then a huge varint name length.
bad = (rp.w_byte(rp.XMIT_LONG_NAME | rp.XMIT_SAME_UID | rp.XMIT_SAME_GID)
       + rp.w_varint(0x7fffff) + b'xx')
bad_log = push([bad])
if 'overflow' not in bad_log:
    test_fail("daemon did NOT hit recv_file_entry's name-length overflow guard "
              "for an XMIT_LONG_NAME entry with a huge varint length; the flag "
              f"or varint encoding is wrong (the parser never saw it).\n"
              f"log:\n{bad_log}")

print("proto-sender-selftest: daemon's recv_file_entry parses rsync_proto's "
      "flags + varint name-length exactly (well-formed accepted, "
      "XMIT_LONG_NAME overflow rejected).")

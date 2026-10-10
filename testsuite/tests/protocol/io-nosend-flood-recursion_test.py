#!/usr/bin/env python3
# Sibling of io-noop-flood-recursion: the same read_a_msg() re-entry bug reached
# through MSG_NO_SEND instead of MSG_NOOP. The original fix deferred
# iobuf.in_multiplexed = 1 only for MSG_NOOP/MSG_IO_ERROR, but the MSG_NO_SEND
# handler also reset it to 1 *before* its send_msg_int(MSG_NO_SEND, val) call-out
# (the !am_generator branch). At a daemon sender (am_sender), send_msg() reaches
# perform_io(PIO_NEED_MSGROOM), which re-enters read_a_msg() while
# in_multiplexed > 0 and iobuf.in.len > 512 -- so a MSG_NO_SEND flood recurses to
# stack exhaustion exactly like the MSG_NOOP one. (Codex review of the run4 0002
# fix flagged this sibling path.)
#
# Pull from a daemon (so it is the sender, not the generator -> the send_msg_int
# branch runs) and flood MSG_NO_SEND frames in one burst. The fix defers
# in_multiplexed = 1 past the call-out here too.
#
# Oracle: pre-fix -> ASan stack-overflow report from the daemon child; fixed ->
# none. Needs an ASan build + a real TCP daemon, and is skipped otherwise.

import glob
import os
import socket
import struct
import time

from rsyncfns import (
    SCRATCHDIR, claim_ports, require_asan, require_tcp, start_rsyncd, test_fail,
)
import rsync_proto as rp

PORT = 12958
require_tcp("the pure-Python client needs a real TCP daemon; run with --use-tcp")
require_asan("the read_a_msg MSG_NO_SEND recursion is observed via the daemon's "
            "AddressSanitizer stack-overflow report")
claim_ports(PORT)

mod = SCRATCHDIR / 'nosend-mod'
mod.mkdir(parents=True, exist_ok=True)
(mod / 'f').write_text("hello\n")

conf = SCRATCHDIR / 'nosend.conf'
# Tiny SO_SNDBUF so the daemon's re-sent MSG_NO_SEND output blocks almost
# immediately (loopback send buffers are otherwise large), forcing send_msg()'s
# perform_io(PIO_NEED_MSGROOM) -- and thus the input-draining re-entry.
conf.write_text(f"""\
pid file = {SCRATCHDIR}/nosend-rsyncd.pid
use chroot = no
socket options = SO_SNDBUF=2048

[mod]
    path = {mod}
    read only = no
""")

asan_log = SCRATCHDIR / 'nosend-asan'
for stale in glob.glob(f"{asan_log}.*"):
    os.unlink(stale)
prev = os.environ.get('ASAN_OPTIONS', '')
os.environ['ASAN_OPTIONS'] = (
    (prev + ':' if prev else '') + f'detect_leaks=0:abort_on_error=1:log_path={asan_log}')

start_rsyncd(conf, PORT)

c = rp.DaemonClient('127.0.0.1', PORT)
# Shrink our receive buffer so the daemon's re-sent MSG_NO_SEND output blocks
# almost immediately (on loopback the default buffers are huge). Once send_msg()
# can't drain, perform_io(PIO_NEED_MSGROOM) does the input-draining re-entry --
# unlike MSG_NOOP, whose maybe_send_keepalive() flushes unconditionally.
c.sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 2048)
# Pull => daemon is the sender (am_sender, not am_generator) => MSG_NO_SEND takes
# the send_msg_int() branch.
c.handshake('mod', ['--server', '--sender', '-e.LsfxCIu', '.', 'mod/'],
            greeting_version=30)
# A MSG_NO_SEND frame: header ((MPLEX_BASE + MSG_NO_SEND) << 24) | 4, 4-byte body.
NOSEND = struct.pack('<I', ((rp.MPLEX_BASE + 102) << 24) | 4) + b'\x00\x00\x00\x00'
try:
    c._send_raw(NOSEND * 60000)
except OSError:
    pass                               # daemon crashed mid-write, or send timed out
try:
    c.drain(timeout=5.0)
except OSError:
    pass                               # connection reset by the crashed daemon child
c.close()

reports = ''
for _ in range(30):
    reports = ''.join(open(r, errors='replace').read()
                      for r in glob.glob(f"{asan_log}.*"))
    if 'AddressSanitizer' in reports:
        break
    time.sleep(0.1)

if 'AddressSanitizer' in reports:
    test_fail(
        "daemon sender recursed through read_a_msg on a MSG_NO_SEND flood and hit "
        "an AddressSanitizer stack-overflow -- the MSG_NO_SEND handler re-armed "
        "in_multiplexed before the send_msg_int flush:\n" + reports[:1500])

print("io-nosend-flood-recursion: a MSG_NO_SEND flood does not re-enter "
      "read_a_msg (no stack exhaustion).")

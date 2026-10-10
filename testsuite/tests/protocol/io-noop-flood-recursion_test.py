#!/usr/bin/env python3
# Regression test for unbounded recursion in read_a_msg() (io.c). read_a_msg()
# sets iobuf.in_multiplexed = -1 on entry so that any perform_io() reached while
# the message body is consumed will not re-enter read_a_msg(). The MSG_NOOP (and
# MSG_IO_ERROR) handlers reset it to 1 *before* calling maybe_send_keepalive() /
# send_msg_int(), both of which can reach perform_io(PIO_NEED_MSGROOM). With more
# frames already buffered (iobuf.in.len > 512), perform_io() then loops back into
# read_a_msg() -- each frame stacks a fresh BIGPATHBUFLEN local, so a hostile peer
# that floods MSG_NOOP at a daemon sender exhausts the stack.
#
# Drive it with the pure-Python client: pull from a daemon (so the daemon is the
# sender, am_sender => maybe_send_keepalive runs) and, instead of a filter list,
# flood MSG_NOOP frames in one burst. The daemon's first read of our stream
# recurses through read_a_msg until the stack guard page is hit.
#
# The fix defers iobuf.in_multiplexed = 1 to *after* the call-out, keeping the
# re-entry guard armed across the write-side flush.
#
# Oracle: pre-fix -> the daemon child hits an ASan stack-overflow report; fixed
# -> none. Needs an ASan build + a real TCP daemon, and is skipped otherwise.

import glob
import os
import struct
import time

from rsyncfns import (
    SCRATCHDIR, claim_ports, require_asan, require_tcp, start_rsyncd, test_fail,
)
import rsync_proto as rp

PORT = 12955
require_tcp("the pure-Python client needs a real TCP daemon; run with --use-tcp")
require_asan("the read_a_msg recursion is observed via the daemon's "
            "AddressSanitizer stack-overflow report")
claim_ports(PORT)

mod = SCRATCHDIR / 'noop-mod'
mod.mkdir(parents=True, exist_ok=True)
(mod / 'f').write_text("hello\n")

conf = SCRATCHDIR / 'noop.conf'
conf.write_text(f"""\
pid file = {SCRATCHDIR}/noop-rsyncd.pid
use chroot = no

[mod]
    path = {mod}
    read only = no
""")

asan_log = SCRATCHDIR / 'noop-asan'
for stale in glob.glob(f"{asan_log}.*"):
    os.unlink(stale)
prev = os.environ.get('ASAN_OPTIONS', '')
os.environ['ASAN_OPTIONS'] = (
    (prev + ':' if prev else '') + f'detect_leaks=0:abort_on_error=1:log_path={asan_log}')

start_rsyncd(conf, PORT)

c = rp.DaemonClient('127.0.0.1', PORT)
# Pull => the daemon is the sender (am_sender, so the MSG_NOOP handler reaches
# maybe_send_keepalive).
c.handshake('mod', ['--server', '--sender', '-e.LsfxCIu', '.', 'mod/'],
            greeting_version=30)
# A MSG_NOOP frame: header ((MPLEX_BASE + MSG_NOOP) << 24) | 0, no payload.
NOOP = struct.pack('<I', (rp.MPLEX_BASE + 42) << 24)
try:
    c._send_raw(NOOP * 40000)          # one big burst => iobuf.in.len >> 512
except OSError:
    pass                               # the daemon may die mid-write
c.drain(timeout=5.0)
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
        "daemon sender recursed through read_a_msg on a MSG_NOOP flood and hit "
        "an AddressSanitizer stack-overflow -- the MSG_NOOP handler re-armed "
        "in_multiplexed before the keepalive flush:\n" + reports[:1500])

print("io-noop-flood-recursion: a MSG_NOOP flood does not re-enter read_a_msg "
      "(no stack exhaustion).")

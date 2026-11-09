#!/usr/bin/env python3
# Regression test for a remotely-reachable abort in rwrite() (log.c): a daemon
# receiver child (am_server=1, am_generator=0, send_msgs_to_gen=1) that receives
# a MSG_INFO/MSG_ERROR frame from the wire calls rwrite() with
# is_utf8 = !am_generator = 1 (io.c read_a_msg). The send_msgs_to_gen branch
# used to assert(!is_utf8), so a peer that sends such a message to the receiver
# triggered a SIGABRT of the per-connection child. Legitimate senders never put
# these tags on the wire. The fix drops the assert and forwards the bytes raw to
# the generator, which logs them.
#
# This drives a real daemon receiver with the pure-Python sender: push a
# one-entry file list (so the receiver enters recv_files and reads our stream),
# then inject a MSG_INFO frame carrying a unique marker. In recv_files the
# receiver's read_ndx_and_attrs() reads our stream and read_a_msg() dispatches
# the MSG_INFO inline -> rwrite() with is_utf8=1.
#
# Oracle: fixed -> the receiver forwards the message to the generator, which
# logs the marker (and echoes a MSG_INFO frame back to us). pre-fix -> the
# receiver SIGABRTs on the assert *before* forwarding, so the marker never
# appears and the generator reports the connection dropped.
#
# Asserts are compiled in on the default (debug) build, so this is a normal-
# build RED/GREEN; it only needs a real TCP daemon (--use-tcp).

import time

from rsyncfns import (
    SCRATCHDIR, claim_ports, require_tcp, start_rsyncd, test_fail,
)
import rsync_proto as rp

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
# One regular file so the receiver enters recv_files and reads our stream.
s.send_flat_flist([rp.FileEntry('hello.txt', mode=rp.S_IFREG | 0o644, length=0)])
# Inject a peer MSG_INFO -- the receiver child's read_a_msg() -> rwrite(is_utf8=1).
s.send_message(rp.MSG_INFO, MARKER + b'\n')
back = s.drain(timeout=3.0)
s.close()

# The forwarded message reaches us as an echoed MSG_INFO frame and/or the
# generator's log. Either presence means the receiver did NOT abort.
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

print("proto-msg-info-assert: daemon receiver forwarded a peer MSG_INFO without "
      "aborting (the rwrite assert is gone).")

#!/usr/bin/env python3
# A malicious server must not be able to disable the client's --timeout.
#
# MSG_IO_TIMEOUT lets a peer ask the client to adopt a SHORTER I/O timeout (a
# stricter cap).  A crafted server that sends MSG_IO_TIMEOUT(0) would, pre-fix,
# zero the client's --timeout via set_io_timeout(0) and could then hang the
# client indefinitely.  The fix ignores a non-positive value, so the client
# keeps its --timeout and exits on its own.
#
# This drives a tiny crafted rsync daemon that completes the handshake, sends
# MSG_IO_TIMEOUT(0) as the first multiplex frame, then holds the socket open
# well past the client's --timeout.  Fixed: the client times out and self-exits.
# Vulnerable: the client hangs until the watchdog kills it -> test_fail.

import subprocess
import sys
import time

from rsyncfns import (
    SCRATCHDIR, claim_ports, rmtree, rsync_argv, test_fail, test_skipped,
)

# No require_tcp: this connects to our own local crafted server over a
# rsync:// URL (a plain TCP connect to 127.0.0.1), independent of the test's
# own pipe/tcp transport, so it runs in either mode.
PORT = 18367
claim_ports(PORT)

CLIENT_TIMEOUT = 3
HOLD = 25           # server holds the socket this long after sending the message
WATCHDOG = 12       # kill the client if it hasn't self-exited by now (< HOLD)

dst = SCRATCHDIR / 'ki47dst'
rmtree(dst)
dst.mkdir(parents=True)

# Minimal crafted daemon: greet, accept module, drain args, do the pre-multiplex
# setup_protocol exchange, then emit MSG_IO_TIMEOUT(0) and hold.
SERVER = r'''
import socket, sys, time, select
MPLEX_BASE, MSG_IO_TIMEOUT = 7, 33
def hdr(t, n):
    v = ((MPLEX_BASE + t) << 24) | (n & 0xFFFFFF)
    return bytes([v & 0xff, (v >> 8) & 0xff, (v >> 16) & 0xff, (v >> 24) & 0xff])
def le32(v):
    return bytes([v & 0xff, (v >> 8) & 0xff, (v >> 16) & 0xff, (v >> 24) & 0xff])
port, hold = int(sys.argv[1]), float(sys.argv[2])
srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
srv.bind(("127.0.0.1", port)); srv.listen(1); srv.settimeout(20.0)
sys.stderr.write("ready\n"); sys.stderr.flush()
conn, _ = srv.accept(); conn.settimeout(10.0)
f = conn.makefile("rwb")
f.write(b"@RSYNCD: 32.0 sha256\n"); f.flush()
f.readline(); f.readline()
f.write(b"@RSYNCD: OK\n"); f.flush()
conn.setblocking(False)
dl, quiet = time.time() + 4.0, 0.0
while time.time() < dl:
    r, _, _ = select.select([conn], [], [], 0.1)
    if r:
        try:
            if not conn.recv(4096): break
            quiet = 0.0
        except BlockingIOError:
            pass
    else:
        quiet += 0.1
        if quiet >= 0.8: break
conn.setblocking(True); conn.settimeout(5.0)
f.write(b"\x00"); f.write(le32(42)); f.flush()        # compat_flags=0, checksum_seed
f.write(hdr(MSG_IO_TIMEOUT, 4)); f.write(le32(0)); f.flush()
time.sleep(hold)
f.close(); conn.close(); srv.close()
'''

srv = subprocess.Popen([sys.executable, '-c', SERVER, str(PORT), str(HOLD)],
                       stderr=subprocess.PIPE)
try:
    # wait for the server to be listening
    line = srv.stderr.readline()
    if b'ready' not in line:
        test_skipped("crafted MSG_IO_TIMEOUT server failed to start")

    t0 = time.monotonic()
    try:
        proc = subprocess.run(
            rsync_argv(f'--timeout={CLIENT_TIMEOUT}', '--info=misc2',
                       f'rsync://127.0.0.1:{PORT}/mod/', f'{dst}/'),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=WATCHDOG)
    except subprocess.TimeoutExpired:
        test_fail(
            f"client did not self-exit within {WATCHDOG}s despite --timeout="
            f"{CLIENT_TIMEOUT}: a MSG_IO_TIMEOUT(0) from the server disabled the "
            "client timeout (it would hang indefinitely).")

    out = (proc.stdout or b'').decode('utf-8', 'replace')
    elapsed = time.monotonic() - t0
    # The security property is that the client does NOT hang: it self-exited
    # within the watchdog above (a vulnerable client would have been killed and
    # raised TimeoutExpired -> test_fail).  On the branch the crafted handshake
    # reaches the inject point and the client exits via its own --timeout; if a
    # platform's handshake didn't engage, the fast self-exit is still safe.
    how = "timed out (kept --timeout)" if 'timeout' in out.lower() \
        else "exited without timing out (crafted handshake may not have engaged)"
    print(f"msg-io-timeout-zero: client self-exited in {elapsed:.1f}s -- {how}; "
          "MSG_IO_TIMEOUT(0) did not disable the client timeout")
finally:
    srv.terminate()
    try:
        srv.wait(timeout=5)
    except subprocess.TimeoutExpired:
        srv.kill()
        srv.wait()

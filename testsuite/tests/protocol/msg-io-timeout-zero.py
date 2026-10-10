#!/usr/bin/env python3

import subprocess
import sys
import time

from harness.rsync import (
    SCRATCHDIR, claim_ports, rmtree, rsync_argv, test_fail, test_skipped,
)

PORT = 18367
claim_ports(PORT)

CLIENT_TIMEOUT = 3
HOLD = 25
WATCHDOG = 12

dst = SCRATCHDIR / 'destination'
rmtree(dst)
dst.mkdir(parents=True)

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
dl, quiet = time.monotonic() + 4.0, 0.0
while time.monotonic() < dl:
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

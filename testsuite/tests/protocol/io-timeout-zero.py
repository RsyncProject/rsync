#!/usr/bin/env python3

import subprocess
import sys
import time

from harness.daemon import claim_ports, require_tcp
from harness.environment import SCRATCHDIR
from harness.filesystem import rmtree
from harness.results import test_fail, test_skipped
from harness.rsync import rsync_argv
from harness import metadata

metadata(features={'tcp'}, transports={'tcp'})

PORT = 18367
claim_ports(PORT)
require_tcp('the crafted server requires a real TCP connection')

CLIENT_TIMEOUT = 3
HOLD = 25
WATCHDOG = 12
TIMEOUT_EXIT = 30

dst = SCRATCHDIR / 'destination'
armed = SCRATCHDIR / 'armed'
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
port, hold, marker = int(sys.argv[1]), float(sys.argv[2]), sys.argv[3]
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
open(marker, "w").close()
time.sleep(hold)
f.close(); conn.close(); srv.close()
'''

srv = subprocess.Popen([sys.executable, '-c', SERVER, str(PORT), str(HOLD), str(armed)],
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
    if not armed.is_file():
        test_fail(f"crafted server never sent MSG_IO_TIMEOUT(0):\n{out}")
    if proc.returncode != TIMEOUT_EXIT or 'timeout' not in out.lower():
        test_fail(f"client did not retain its timeout after MSG_IO_TIMEOUT(0): "
                  f"rc={proc.returncode}, elapsed={elapsed:.1f}s\n{out}")
    if elapsed < CLIENT_TIMEOUT - 0.5:
        test_fail(f"client exited after {elapsed:.1f}s, before its {CLIENT_TIMEOUT}s timeout")
    print(f"io-timeout-zero: client timed out in {elapsed:.1f}s after "
          "MSG_IO_TIMEOUT(0)")
finally:
    srv.terminate()
    try:
        srv.wait(timeout=5)
    except subprocess.TimeoutExpired:
        srv.kill()
        srv.wait()

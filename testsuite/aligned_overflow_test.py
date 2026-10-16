#!/usr/bin/env python3
"""aligned_i overflow in hash_search() (--inplace > 2 GiB). Reported by Mominul Hemal

Verifies that when `aligned_offset < offset` advances `aligned_i` in
hash_search(), `aligned_i` is bounded by `s->count` and cannot overflow a
32-bit signed integer on files > 2 GiB.
"""

import socket
import struct
import subprocess
import threading

from rsyncfns import FROMDIR, TODIR, TOOLDIR, rmtree, test_fail

RSYNC_BIN = str(TOOLDIR / 'rsync')

MPLEX_BASE = 7
MSG_DATA = 0
NDX_DONE = -1
ITEM_TRANSFER = 1 << 15


def w_shortint(x: int) -> bytes:
    return struct.pack('<H', x & 0xFFFF)


def w_int(x: int) -> bytes:
    return struct.pack('<i', x & 0xFFFFFFFF if x >= 0 else x)


def w_varint(x: int) -> bytes:
    if x < 0:
        x = (1 << 32) + x
    b = bytearray()
    while True:
        b.append(x & 0x7F)
        x >>= 7
        if x == 0:
            break
        b[-1] |= 0x80
    return bytes(b)


def w_sum_head(count: int, blength: int, s2length: int, remainder: int) -> bytes:
    return w_int(count) + w_int(blength) + w_int(s2length) + w_int(remainder)


def get_checksum1(buf: bytes) -> int:
    sb = [c - 256 if c >= 128 else c for c in buf]
    s1 = s2 = 0
    for val in sb:
        s1 = (s1 + val) & 0xFFFFFFFF
        s2 = (s2 + s1) & 0xFFFFFFFF
    return ((s1 & 0xFFFF) + ((s2 & 0xFFFF) << 16)) & 0xFFFFFFFF


class MinimalDaemonReceiver:
    def __init__(self, sock: socket.socket):
        self.sock = sock
        self._ndx_prev_positive = -1

    def _readline(self) -> str:
        line = bytearray()
        while not line.endswith(b'\n'):
            c = self.sock.recv(1)
            if not c:
                raise IOError("EOF reading line")
            line += c
        return line.decode('latin-1').rstrip('\n')

    def handshake(self):
        self.sock.sendall(b"@RSYNCD: 30.0\n")
        self._readline()  # client greeting
        self._readline()  # module name
        self.sock.sendall(b"@RSYNCD: OK\n")
        self.sock.sendall(w_varint(0) + w_int(0))

    def send_data(self, payload: bytes):
        hdr = struct.pack('<I', ((MPLEX_BASE + MSG_DATA) << 24) | len(payload))
        self.sock.sendall(hdr + payload)

    def w_ndx(self, ndx: int) -> bytes:
        if ndx == NDX_DONE:
            return b'\x00'
        diff = ndx - self._ndx_prev_positive
        self._ndx_prev_positive = ndx
        return bytes([diff])


BIG_SIZE = (1 << 31) + 64       # 2 GiB + 64 bytes
NEEDLE_OFFSET = (1 << 31) + 8   # Matching byte past 2^31
NEEDLE_BYTE = b'\x01'

rmtree(FROMDIR)
rmtree(TODIR)
FROMDIR.mkdir(parents=True, exist_ok=True)
TODIR.mkdir(parents=True, exist_ok=True)

bigfile = FROMDIR / 'bigfile.bin'
out_log = TODIR / 'client.log'

with open(bigfile, 'wb') as f:
    f.truncate(BIG_SIZE)
    f.seek(0)
    f.write(NEEDLE_BYTE)
    f.seek(NEEDLE_OFFSET)
    f.write(NEEDLE_BYTE)

lsock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
lsock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
lsock.bind(('127.0.0.1', 0))
port = lsock.getsockname()[1]
lsock.listen(1)
lsock.settimeout(30)

csock = None
t = None
rc = None
logf = open(out_log, 'wb')
proc = subprocess.Popen(
    [
        RSYNC_BIN, '-a', '--inplace', '--debug=deltasum1',
        str(bigfile), f'rsync://127.0.0.1:{port}/mod/',
    ],
    stdout=logf,
    stderr=logf,
)

try:
    csock, _ = lsock.accept()
    receiver = MinimalDaemonReceiver(csock)
    receiver.handshake()

    def drain_stream():
        try:
            csock.settimeout(90)
            while csock.recv(1 << 20):
                pass
        except OSError:
            pass

    t = threading.Thread(target=drain_stream, daemon=True)
    t.start()

    sum1 = get_checksum1(NEEDLE_BYTE)
    # Send transfer request followed by 4 NDX_DONEs (phase 1, phase 2,
    # end of send_files, and final goodbye) so the sender exits cleanly
    # as soon as hash_search() completes.
    buf = (
        receiver.w_ndx(0)
        + w_shortint(ITEM_TRANSFER)
        + w_sum_head(1, 1, 0, 0)
        + w_int(sum1)
        + (receiver.w_ndx(NDX_DONE) * 4)
    )
    receiver.send_data(buf)

    rc = proc.wait(timeout=120)
    t.join(timeout=5)
finally:
    if csock is not None:
        try:
            csock.close()
        except OSError:
            pass
    lsock.close()
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
    if t is not None:
        t.join(timeout=5)
    logf.close()
    bigfile.unlink(missing_ok=True)

out_text = out_log.read_text(encoding='utf-8', errors='replace')
print(f"sender exited with rc={rc}", flush=True)
print(out_text[-1000:], flush=True)

if rc != 0 or 'total: matches=' not in out_text:
    test_fail(
        f"sender crashed or did not complete hash_search() past 2 GiB "
        f"(rc={rc}):\n{out_text[-1000:]}"
    )

print("aligned_overflow test passed: completed >2 GiB hash_search() cleanly", flush=True)

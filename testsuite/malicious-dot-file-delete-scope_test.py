#!/usr/bin/env python3
"""A malicious sender must not reinterpret the destination root as a file.

The receiver permits the synthetic "." entry as a top-level directory even
when an implied-file filter restricts the requested source.  This server sends
that special name with regular-file mode.  Under --force, replacing it must not
recursively empty the receiver's destination root.  No --delete is requested.
"""

import socket
import subprocess
import threading

from rsyncfns import (
    SCRATCHDIR, claim_ports, makepath, require_tcp, rmtree, rsync_argv,
    test_fail,
)
import rsync_proto as rp

PORT = 13012

require_tcp("the malicious sender needs a real TCP socket; run with --use-tcp")
claim_ports(PORT)

base = SCRATCHDIR / 'malicious-dot-file-delete-scope'
rmtree(base)
dest = base / 'dest'
makepath(dest / 'private')
sentinel = dest / 'private' / 'must-survive'
sentinel.write_text('receiver-owned file outside the requested leaf\n')

# Native control: the same scoped single-file transfer with --force does not
# touch receiver-owned siblings when the sender encodes the requested leaf
# honestly.  This distinguishes the attack from documented --force semantics.
honest_src = base / 'honest-src'
makepath(honest_src)
(honest_src / 'requested').write_text('honest source\n')
control = subprocess.run(
    rsync_argv('-r', '--force', '--no-inc-recursive',
               str(honest_src / 'requested'), str(dest) + '/'),
    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30)
if control.returncode or not sentinel.exists():
    test_fail('native --force single-file control unexpectedly touched the '
              f'destination scope:\n{control.stdout}{control.stderr}')

lsock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
lsock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
lsock.bind(('127.0.0.1', PORT))
lsock.listen(1)
lsock.settimeout(30)
state = {}


def serve():
    try:
        csock, _ = lsock.accept()
        s = rp.DaemonReceiver(csock)
        s.handshake()

        # "." is the receiver's synthetic transfer-root entry and is exempted
        # from its requested-name filter.  Forge it as a regular file instead
        # of the only legitimate representation: a directory.
        s.send_data(
            rp.FileEntry('.', mode=rp.S_IFREG | 0o644, length=0).encode()
            + rp.end_of_flist(0, s.protocol)
            + s.w_ndx(rp.NDX_DONE) * 3
        )
        s.drain(timeout=5)
        s.close()
    except Exception as exc:  # noqa: BLE001 - reported below
        state['err'] = repr(exc)


t = threading.Thread(target=serve, daemon=True)
t.start()
try:
    proc = subprocess.run(
        rsync_argv('-r', '--force', '--no-inc-recursive',
                   f'rsync://127.0.0.1:{PORT}/mod/requested', str(dest) + '/'),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30)
finally:
    t.join(timeout=10)
    lsock.close()

if not sentinel.exists():
    test_fail(
        'malicious sender encoded the special "." root as a regular file and '
        'made --force recursively erase the destination root')
if state.get('err'):
    test_fail(f"Python sender failed before exercising receiver: {state['err']}\n"
              f"receiver output:\n{proc.stdout}{proc.stderr}")

print('malicious-dot-file-delete-scope: receiver rejected a non-directory '
      'encoding of its synthetic transfer-root entry')

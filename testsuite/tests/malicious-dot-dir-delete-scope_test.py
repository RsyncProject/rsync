#!/usr/bin/env python3
"""A malicious pull server must not enlarge --delete to the transfer root.

For a scoped single-file pull, the synthetic ``.`` entry is not part of the
requested source.  The receiver nevertheless exempts it from its requested-name
filter.  A hostile sender can encode it as a top-level content directory and
make the generator run delete_in_dir("."), sweeping receiver-owned siblings.
"""

import socket
import subprocess
import threading

from rsyncfns import (
    SCRATCHDIR, claim_ports, makepath, require_tcp, rmtree, rsync_argv,
    test_fail,
)
import rsync_proto as rp

PORT = 13014

require_tcp("the malicious sender needs a real TCP socket; run with --use-tcp")
claim_ports(PORT)

base = SCRATCHDIR / 'malicious-dot-dir-delete-scope'
rmtree(base)
dest = base / 'dest'
makepath(dest / 'private')
sentinel = dest / 'private' / 'must-survive'
sentinel.write_text('receiver-owned file outside the requested leaf\n')

# Control the intended scope: an honest single-file source with identical
# receiver options does not make --delete recurse over destination siblings.
honest_src = base / 'honest-src'
makepath(honest_src)
(honest_src / 'requested').write_text('honest source\n')
control = subprocess.run(
    rsync_argv('-r', '--delete', '--no-inc-recursive',
               str(honest_src / 'requested'), str(dest) + '/'),
    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30)
if control.returncode or not sentinel.exists():
    test_fail('native --delete single-file control unexpectedly swept the '
              f'destination root:\n{control.stdout}{control.stderr}')

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

        # XMIT_TOP_DIR without XMIT_NO_CONTENT_DIR maps the synthetic dot to
        # FLAG_CONTENT_DIR.  "requested" is the only name the client asked for.
        flist = (
            rp.FileEntry('.', mode=rp.S_IFDIR | 0o755,
                         extra_flags=rp.XMIT_TOP_DIR).encode()
            + rp.FileEntry('requested', mode=rp.S_IFREG | 0o644,
                           length=0).encode()
            + rp.end_of_flist(0, s.protocol)
        )
        # No file bytes are needed: delete_in_dir(".") runs in the generator
        # before the transfer response is consumed.
        s.send_data(flist + s.w_ndx(rp.NDX_DONE) * 3)
        s.drain(timeout=5)
        s.close()
    except Exception as exc:  # noqa: BLE001 - reported below
        state['err'] = repr(exc)


t = threading.Thread(target=serve, daemon=True)
t.start()
try:
    proc = subprocess.run(
        rsync_argv('-r', '--delete', '--no-inc-recursive',
                   f'rsync://127.0.0.1:{PORT}/mod/requested', str(dest) + '/'),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30)
finally:
    t.join(timeout=10)
    lsock.close()

if not sentinel.exists():
    test_fail(
        'malicious sender marked the exempt synthetic dot as a content '
        'directory and expanded a scoped single-file --delete to the entire '
        'destination root')
if state.get('err'):
    test_fail(f"Python sender failed before exercising receiver: {state['err']}\n"
              f"receiver output:\n{proc.stdout}{proc.stderr}")

print('malicious-dot-dir-delete-scope: receiver did not let a forged dot '
      'content directory expand --delete beyond the requested leaf')

#!/usr/bin/env python3

import socket
import subprocess
import threading

from harness import protocol as rp
from harness.rsync import SCRATCHDIR, claim_ports, makepath, require_tcp, rmtree, rsync_argv, test_fail

PORTS = 13012, 13014
require_tcp('the malicious sender requires TCP')
claim_ports(*PORTS)

def check(name, port, option, entries):
    base = SCRATCHDIR / name
    destination = base / 'destination'
    source = base / 'source'
    sentinel = destination / 'private' / 'must-survive'
    rmtree(base)
    makepath(sentinel.parent, source)
    sentinel.write_text('receiver-owned\n')
    (source / 'requested').write_text('source\n')
    control = subprocess.run(
        rsync_argv('-r', option, '--no-inc-recursive', str(source / 'requested'),
                   f'{destination}/'),
        capture_output=True, text=True, timeout=30)
    if control.returncode or not sentinel.exists():
        test_fail(f'{name}: native control changed the destination scope')

    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(('127.0.0.1', port))
    listener.listen(1)
    listener.settimeout(30)
    error = []

    def serve():
        try:
            client, _ = listener.accept()
            sender = rp.DaemonReceiver(client)
            sender.handshake()
            sender.send_data(entries(sender) + sender.w_ndx(rp.NDX_DONE) * 3)
            sender.drain(timeout=5)
            sender.close()
        except Exception as exc:
            error.append(repr(exc))

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    try:
        result = subprocess.run(
            rsync_argv('-r', option, '--no-inc-recursive',
                       f'rsync://127.0.0.1:{port}/mod/requested', f'{destination}/'),
            capture_output=True, text=True, timeout=30)
    finally:
        thread.join(timeout=10)
        listener.close()

    if not sentinel.exists():
        test_fail(f'{name}: a forged dot entry erased receiver-owned data')
    if error:
        test_fail(f'{name}: malicious sender failed: {error[0]}\n{result.stdout}{result.stderr}')

def dot_file(sender):
    return (rp.FileEntry('.', mode=rp.S_IFREG | 0o644, length=0).encode()
            + rp.end_of_flist(0, sender.protocol))

def dot_directory(sender):
    return (rp.FileEntry('.', mode=rp.S_IFDIR | 0o755,
                         extra_flags=rp.XMIT_TOP_DIR).encode()
            + rp.FileEntry('requested', mode=rp.S_IFREG | 0o644, length=0).encode()
            + rp.end_of_flist(0, sender.protocol))

check('dot-file', PORTS[0], '--force', dot_file)
check('dot-directory', PORTS[1], '--delete', dot_directory)
print('forged dot entries remain scoped to the requested source')

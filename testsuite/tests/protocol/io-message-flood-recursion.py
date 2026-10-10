#!/usr/bin/env python3

import glob
import os
import socket
import struct
import time

from harness import protocol as rp
from harness.rsync import SCRATCHDIR, claim_ports, require_asan, require_tcp, start_rsyncd, test_fail

PORTS = 12955, 12958
require_tcp('the protocol client requires TCP')
require_asan('message recursion is detected through AddressSanitizer')
claim_ports(*PORTS)
asan_options = os.environ.get('ASAN_OPTIONS', '')

def check(name, port, frame, count, small_buffers=False):
    module = SCRATCHDIR / f'{name}-module'
    module.mkdir(parents=True)
    (module / 'f').write_text('data\n')
    socket_options = 'socket options = SO_SNDBUF=2048\n' if small_buffers else ''
    config = SCRATCHDIR / f'{name}.conf'
    config.write_text(f'''pid file = {SCRATCHDIR}/{name}.pid
use chroot = no
{socket_options}
[module]
    path = {module}
    read only = no
''')
    log = SCRATCHDIR / f'{name}-asan'
    for stale in glob.glob(f'{log}.*'):
        os.unlink(stale)
    os.environ['ASAN_OPTIONS'] = ((asan_options + ':') if asan_options else '') + (
        f'detect_leaks=0:abort_on_error=1:log_path={log}')
    start_rsyncd(config, port)

    client = rp.DaemonClient('127.0.0.1', port)
    if small_buffers:
        client.sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 2048)
    client.handshake('module', ['--server', '--sender', '-e.LsfxCIu', '.', 'module/'],
                     greeting_version=30)
    try:
        client._send_raw(frame * count)
        client.drain(timeout=5)
    except OSError:
        pass
    client.close()

    reports = ''
    for _ in range(30):
        reports = ''.join(open(path, errors='replace').read()
                          for path in glob.glob(f'{log}.*'))
        if 'AddressSanitizer' in reports:
            break
        time.sleep(0.1)
    if 'AddressSanitizer' in reports:
        test_fail(f'{name} flood re-entered read_a_msg():\n{reports[:1500]}')

noop = struct.pack('<I', (rp.MPLEX_BASE + 42) << 24)
no_send = struct.pack('<I', ((rp.MPLEX_BASE + rp.MSG_NO_SEND) << 24) | 4) + b'\0\0\0\0'
check('noop', PORTS[0], noop, 40_000)
check('no-send', PORTS[1], no_send, 60_000, small_buffers=True)
print('message floods do not recurse through read_a_msg')

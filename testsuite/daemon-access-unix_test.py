#!/usr/bin/env python3
"""Daemon access control recognizes Unix-domain client addresses."""

import os
import socket
import subprocess

from rsyncfns import (
    SCRATCHDIR, rsync_argv, test_fail, test_skipped, write_daemon_conf,
)

if not hasattr(socket, 'AF_UNIX') or not hasattr(socket, 'socketpair'):
    test_skipped('Unix-domain socket pairs are unavailable')

module_path = SCRATCHDIR / 'access-unix-module'
module_path.mkdir(parents=True, exist_ok=True)
conf = write_daemon_conf([
    ('allow-unix', {
        'path': str(module_path), 'read only': 'yes', 'hosts allow': 'unix:',
    }),
    ('deny-unix', {
        'path': str(module_path), 'read only': 'yes', 'hosts deny': 'unix:',
    }),
    ('allow-ip', {
        'path': str(module_path), 'read only': 'yes',
        'hosts allow': '127.0.0.1',
    }),
], globals={'hosts allow': ''})

def module_response(name):
    """Return the response to a module request over an AF_UNIX socket pair."""
    try:
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    except (OSError, ValueError) as e:
        test_skipped(f'Unix-domain sockets are unavailable: {e}')

    randomAddress = f'{SCRATCHDIR}/rsyncd.sock'
    os.unlink(randomAddress) if os.path.exists(randomAddress) else None
    listener.bind(randomAddress)
    listener.listen(1)
    client.connect(randomAddress)
    server, _ = listener.accept()

    proc = None
    try:
        proc = subprocess.Popen(
            rsync_argv('--daemon', '--no-detach', f'--config={conf}'),
            stdin=server.fileno(), stdout=server.fileno(),
            stderr=subprocess.PIPE, close_fds=True,
        )
        server.close()
        client.settimeout(10)
        stream = client.makefile('rwb', buffering=0)
        greeting = stream.readline()
        if not greeting.startswith(b'@RSYNCD: '):
            test_fail(f'unexpected daemon greeting: {greeting!r}')
        stream.write(b'@RSYNCD: 31.0\n')
        stream.write(name.encode('ascii') + b'\n')
        response = stream.readline()
        stream.close()
        return response
    finally:
        client.close()
        server.close()
        if proc is not None:
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.terminate()
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=2)


for name in ('allow-unix',):
    response = module_response(name)
    if response != b'@RSYNCD: OK\n':
        test_fail(f'{name} should allow a Unix-domain client, got {response!r}')

for name in ('deny-unix', 'allow-ip'):
    response = module_response(name)
    if not response.startswith(b'@ERROR: access denied'):
        test_fail(f'{name} should deny a Unix-domain client, got {response!r}')

print('daemon-access-unix: hosts allow/deny matching verified')

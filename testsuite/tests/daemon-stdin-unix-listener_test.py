#!/usr/bin/env python3
"""A named Unix listener on stdin selects inetd-style daemon mode."""

import os
import socket
import subprocess
import sys
import tempfile

from rsyncfns import (
    SCRATCHDIR, claim_free_port, rmtree, rsync_argv, test_fail,
    test_skipped, write_daemon_conf,
)

if not hasattr(socket, 'AF_UNIX'):
    test_skipped('Unix-domain sockets are unavailable', capability='unix_listener')
if sys.platform == 'cygwin':
    test_skipped('Cygwin cannot pass a named Unix listener as daemon stdin',
                 capability='unix_listener')

base = SCRATCHDIR / 'daemon-stdin-unix-listener'
rmtree(base)
module = base / 'module'
module.mkdir(parents=True)
conf = write_daemon_conf([
    ('module', {'path': str(module), 'read only': 'yes'}),
])
port = claim_free_port(12980)


def check_listener(address, label):
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    child = None
    proc = None
    try:
        try:
            listener.bind(address)
            listener.listen(1)
            client.connect(address)
            child, _ = listener.accept()
        except (OSError, ValueError) as e:
            test_skipped(f'{label} Unix listener is unavailable: {e}', capability='unix_listener')

        proc = subprocess.Popen(
            rsync_argv('--daemon', '--no-detach', f'--config={conf}',
                       f'--port={port}'),
            stdin=child.fileno(), stdout=child.fileno(),
            stderr=subprocess.PIPE, close_fds=True,
        )
        child.close()
        child = None

        client.settimeout(2)
        try:
            greeting = client.recv(256)
        except socket.timeout:
            test_fail(f'{label}: daemon did not greet the accepted Unix connection')
        if not greeting.startswith(b'@RSYNCD:'):
            test_fail(f'{label}: unexpected daemon greeting: {greeting!r}')
    finally:
        if child is not None:
            child.close()
        client.close()
        listener.close()
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


with tempfile.TemporaryDirectory(prefix='rsync-listener-') as tmpdir:
    check_listener(f'{tmpdir}/rsyncd.sock', 'filesystem')

if sys.platform.startswith('linux'):
    # A leading NUL selects Linux's abstract Unix namespace.
    check_listener(f'\0rsync-stdin-listener-{os.getpid()}', 'abstract')

print('named Unix stdin listeners select inetd-style daemon mode')

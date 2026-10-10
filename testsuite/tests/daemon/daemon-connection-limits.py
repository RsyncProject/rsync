#!/usr/bin/env python3

import socket
import time

from harness.rsync import (
    FROMDIR, RSYNC, SCRATCHDIR, claim_ports, make_tree, rmtree, start_rsyncd,
    test_fail, write_daemon_conf,
)

PORT = 13022
claim_ports(PORT)

rmtree(FROMDIR)
make_tree(FROMDIR, depth=1)
conf = write_daemon_conf([
    ('limited', {
        'path': str(FROMDIR),
        'read only': 'yes',
        'max connections': '1',
        'lock file': str(SCRATCHDIR / 'connections.lock'),
    }),
], name='connection-limits.conf')
start_rsyncd(conf, PORT, rsync_cmd=RSYNC)

def recv_line(sock):
    data = bytearray()
    while not data.endswith(b'\n'):
        chunk = sock.recv(1)
        if not chunk:
            break
        data += chunk
        if len(data) > 4096:
            test_fail(f'daemon response line is too long: {bytes(data)!r}')
    return bytes(data)

def open_module(module):
    sock = socket.create_connection(('127.0.0.1', PORT), timeout=10)
    greeting = recv_line(sock)
    if not greeting.startswith(b'@RSYNCD:'):
        sock.close()
        test_fail(f'unexpected daemon greeting: {greeting!r}')
    sock.sendall(f'@RSYNCD: 31.0\n{module}\n'.encode())
    return sock, recv_line(sock)

first, reply = open_module('limited')
if reply != b'@RSYNCD: OK\n':
    first.close()
    test_fail(f'first limited connection was not accepted: {reply!r}')

second, reply = open_module('limited')
second.close()
if b'max connections (1) reached' not in reply:
    first.close()
    test_fail(f'second limited connection was not rejected: {reply!r}')

first.close()

deadline = time.monotonic() + 10
while True:
    third, reply = open_module('limited')
    if reply == b'@RSYNCD: OK\n':
        third.close()
        break
    third.close()
    if time.monotonic() >= deadline:
        test_fail(f'connection slot was not released after disconnect: {reply!r}')
    time.sleep(0.05)

print('daemon enforces max connections and releases its lock after disconnect')

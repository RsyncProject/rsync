#!/usr/bin/env python3

import os
import socket
import subprocess
import threading

from harness.daemon import claim_ports, require_tcp
from harness.rsync import SCRATCHDIR, rsync_argv, test_fail

def start_proxy(port, response):
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(('127.0.0.1', port))
    listener.listen(1)

    def serve():
        connection, _ = listener.accept()
        try:
            connection.recv(65536)
            connection.sendall(response)
        finally:
            connection.close()
            listener.close()

    threading.Thread(target=serve, daemon=True).start()

def probe_proxy(port, host, expected):
    environment = {**os.environ, 'RSYNC_PROXY': f'127.0.0.1:{port}'}
    result = subprocess.run(
        rsync_argv(f'rsync://{host}/mod/', str(SCRATCHDIR / 'proxy-out')),
        capture_output=True, text=True, env=environment,
    )
    output = result.stdout + result.stderr
    if result.returncode == 0:
        test_fail(f'proxy probe unexpectedly succeeded:\n{output}')
    if expected not in output:
        test_fail(f'expected {expected!r} in proxy probe output:\n{output}')

cases = (
    (12931, 'a' * 1500 + '.invalid', b'', 'proxy CONNECT request too long'),
    (12932, 'example.invalid', b'HTTP/1.0 200 OK\r\n' + b'X' * 1023,
     'proxy response header line too long'),
    (12873, 'example.invalid', b'X' * 1023, 'proxy response line too long'),
)
require_tcp('proxy tests require TCP')
claim_ports(*(port for port, _, _, _ in cases))

for port, host, response, error in cases:
    start_proxy(port, response)
    probe_proxy(port, host, error)

print('oversized proxy requests and responses are rejected')

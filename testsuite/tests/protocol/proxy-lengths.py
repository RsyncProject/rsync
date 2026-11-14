#!/usr/bin/env python3

import os
import socket
import subprocess
import threading

from harness.daemon import claim_ports, require_tcp
from harness.environment import SCRATCHDIR
from harness.results import test_fail
from harness.rsync import rsync_argv
from harness import metadata

metadata(features={'tcp'}, transports={'tcp'})

def start_proxy(port, response):
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(('127.0.0.1', port))
    listener.listen(1)
    listener.settimeout(5)
    state = {'connected': False}

    def serve():
        try:
            connection, _ = listener.accept()
            state['connected'] = True
            with connection:
                connection.settimeout(5)
                state['request'] = connection.recv(65536)
                connection.sendall(response)
        except socket.timeout:
            pass
        except OSError as error:
            state['error'] = repr(error)
        finally:
            listener.close()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    return thread, state

def probe_proxy(port, host, expected):
    environment = {**os.environ, 'RSYNC_PROXY': f'127.0.0.1:{port}'}
    result = subprocess.run(
        rsync_argv(f'rsync://{host}/mod/', str(SCRATCHDIR / 'proxy-out')),
        capture_output=True, text=True, env=environment, timeout=30,
    )
    output = result.stdout + result.stderr
    if result.returncode == 0:
        test_fail(f'proxy probe unexpectedly succeeded:\n{output}')
    if expected not in output:
        test_fail(f'expected {expected!r} in proxy probe output:\n{output}')

cases = (
    (12931, 'a' * 1500 + '.invalid', b'', 'proxy CONNECT request too long', False),
    (12932, 'example.invalid', b'HTTP/1.0 200 OK\r\n' + b'X' * 1023,
     'proxy response header line too long', True),
    (12873, 'example.invalid', b'X' * 1023, 'proxy response line too long', True),
)
require_tcp('proxy tests require TCP')
claim_ports(*(port for port, _, _, _, _ in cases))

for port, host, response, error, should_send in cases:
    thread, state = start_proxy(port, response)
    probe_proxy(port, host, error)
    thread.join(timeout=6)
    if thread.is_alive():
        test_fail(f'{error}: proxy fixture did not stop')
    if state.get('error'):
        test_fail(f'{error}: proxy fixture failed: {state["error"]}')
    if bool(state.get('request')) != should_send:
        test_fail(f'{error}: proxy request delivery was {bool(state.get("request"))}, expected {should_send}')

print('oversized proxy requests and responses are rejected')

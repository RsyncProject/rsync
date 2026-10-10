#!/usr/bin/env python3

import socket
import subprocess

from harness.daemon_config import write_daemon_conf
from harness.rsync import SCRATCHDIR, claim_free_port, rmtree, rsync_argv, test_fail

base = SCRATCHDIR / 'daemon-stdin-inetd'
rmtree(base)
module = base / 'module'
module.mkdir(parents=True)
conf = write_daemon_conf([
    ('module', {'path': str(module), 'read only': 'yes'}),
])

def check_inetd(family, address, label, fallback_port):
    listener = socket.socket(family, socket.SOCK_STREAM)
    client = socket.socket(family, socket.SOCK_STREAM)
    accepted = None
    proc = None
    try:
        if family == socket.AF_INET6:
            listener.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
        listener.bind(address)
        listener.listen(1)
        client.settimeout(10)
        client.connect(listener.getsockname())
        accepted, _ = listener.accept()

        proc = subprocess.Popen(
            rsync_argv('--daemon', '--no-detach', f'--config={conf}',
                       f'--port={fallback_port}'),
            stdin=accepted, stdout=accepted,
            stderr=subprocess.PIPE, close_fds=True,
        )
        accepted.close()
        accepted = None

        try:
            greeting = client.recv(256)
        except socket.timeout:
            test_fail(f'{label}: daemon did not greet the connected stdin socket')
        if not greeting.startswith(b'@RSYNCD:'):
            test_fail(f'{label}: unexpected daemon greeting: {greeting!r}')

        client.sendall(b'@RSYNCD: 31.0\n#exit\n')
        try:
            client.shutdown(socket.SHUT_WR)
        except OSError:
            pass
        try:
            while client.recv(4096):
                pass
        except OSError:
            pass

        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            test_fail(f'{label}: inetd-style daemon did not exit after #exit')
    finally:
        if accepted is not None:
            accepted.close()
        client.close()
        listener.close()
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=2)

check_inetd(socket.AF_INET, ('127.0.0.1', 0), 'IPv4',
            claim_free_port(12980))

ipv6_tested = False
if socket.has_ipv6:
    try:
        with socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as probe:
            probe.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
            probe.bind(('::1', 0))
    except OSError as error:
        print(f'IPv6 loopback is unavailable: {error}')
    else:
        check_inetd(socket.AF_INET6, ('::1', 0), 'IPv6',
                    claim_free_port(12981))
        ipv6_tested = True

print('connected IPv4 stdin selects inetd mode' +
      ('; connected IPv6 stdin selects inetd mode' if ipv6_tested else ''))

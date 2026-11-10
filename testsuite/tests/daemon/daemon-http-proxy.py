#!/usr/bin/env python3

import base64
import os
import select
import socket
import subprocess
import threading

from harness.daemon_config import write_daemon_conf
from harness.daemon import claim_ports, require_tcp, start_test_daemon
from harness.environment import FROMDIR, SCRATCHDIR
from harness.filesystem import make_tree, makepath, rmtree
from harness.results import test_fail
from harness.rsync import rsync_argv
from harness import metadata

metadata(features={'tcp'}, transports={'tcp'})

require_tcp("RSYNC_PROXY uses open_socket_out(), needs a real TCP connect")

DAEMON_PORT = 19880
PROXY_PORT = 19881
claim_ports(DAEMON_PORT, PROXY_PORT)

src = FROMDIR
dst = SCRATCHDIR / 'dest-httpproxy'
rmtree(src)
rmtree(dst)
make_tree(src, depth=1)
makepath(dst)

conf = write_daemon_conf(
    [('mod', {'path': str(dst), 'read only': 'no'})],
    name='http-proxy.conf',
)
url = start_test_daemon(conf, DAEMON_PORT)

seen_connects = []
seen_auths = []
deny_next = threading.Event()
stopping = threading.Event()
workers = []

def relay(a, b):
    socks = [a, b]
    while True:
        r, _, _ = select.select(socks, [], [], 5)
        if not r:
            break
        done = False
        for s in r:
            try:
                buf = s.recv(65536)
            except OSError:
                buf = b''
            if not buf:
                done = True
                break
            (b if s is a else a).sendall(buf)
        if done:
            break
def proxy_handle(cli):
    with cli:
        cli.settimeout(5)
        hdr = b''
        while b'\r\n\r\n' not in hdr and len(hdr) < 4096:
            c = cli.recv(1)
            if not c:
                return
            hdr += c
        lines = hdr.split(b'\r\n')
        seen_connects.append(lines[0].decode('ascii', 'replace'))
        for ln in lines[1:]:
            if ln.lower().startswith(b'proxy-authorization:'):
                seen_auths.append(ln.decode('ascii', 'replace'))
        if deny_next.is_set():
            deny_next.clear()
            cli.sendall(b'HTTP/1.0 503 Service Unavailable\r\n\r\n')
            return
        cli.sendall(b'HTTP/1.0 200 Connection established\r\n\r\n')
        with socket.create_connection(('127.0.0.1', DAEMON_PORT), timeout=5) as up:
            relay(cli, up)

def proxy_loop(lsock):
    while not stopping.is_set():
        try:
            c, _ = lsock.accept()
        except socket.timeout:
            continue
        except OSError:
            return
        worker = threading.Thread(target=proxy_handle, args=(c,))
        workers.append(worker)
        worker.start()

lsock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
lsock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
lsock.bind(('127.0.0.1', PROXY_PORT))
lsock.listen(8)
lsock.settimeout(0.2)
listener = threading.Thread(target=proxy_loop, args=(lsock,))
listener.start()

def push_via_proxy(proxy_spec, expect_ok=True):
    env = dict(os.environ, RSYNC_PROXY=proxy_spec)
    r = subprocess.run(
        rsync_argv('-r', f'{src}/',
                   f'rsync://upstream.example:{DAEMON_PORT}/mod/'),
        env=env, capture_output=True, text=True,
    )
    if expect_ok and r.returncode != 0:
        test_fail(f"RSYNC_PROXY={proxy_spec!r}: rc={r.returncode}\n{r.stderr}")
    if not expect_ok and r.returncode == 0:
        test_fail(f"RSYNC_PROXY={proxy_spec!r}: expected failure, got rc=0")
    return r

try:
    push_via_proxy(f'127.0.0.1:{PROXY_PORT}')
    if not seen_connects or 'CONNECT upstream.example:' not in seen_connects[-1]:
        test_fail(f"mock proxy never saw CONNECT (got: {seen_connects!r})")

    seen_auths.clear()
    push_via_proxy(f'puser:ppass@127.0.0.1:{PROXY_PORT}')
    if not seen_auths:
        test_fail("RSYNC_PROXY=user:pass@... should have sent Proxy-Authorization")
    want = 'Basic ' + base64.b64encode(b'puser:ppass').decode()
    if want not in seen_auths[-1]:
        test_fail(f"Proxy-Authorization mismatch: {seen_auths[-1]!r} vs {want!r}")

    deny_next.set()
    r = push_via_proxy(f'127.0.0.1:{PROXY_PORT}', expect_ok=False)
    if 'bad response from proxy' not in r.stderr and '503' not in r.stderr:
        test_fail(f"503 from proxy should surface as 'bad response': {r.stderr!r}")
finally:
    stopping.set()
    lsock.close()
    listener.join(timeout=5)
    for worker in workers:
        worker.join(timeout=6)
    if any(thread.is_alive() for thread in (listener, *workers)):
        test_fail('mock proxy did not stop')
print(f"daemon-http-proxy: CONNECT (no-auth + Basic auth) + 503-deny ok")

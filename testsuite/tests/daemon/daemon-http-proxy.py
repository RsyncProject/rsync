#!/usr/bin/env python3

import base64
import os
import select
import socket
import subprocess
import threading

from harness.rsync import (
    SCRATCHDIR, FROMDIR,
    claim_ports, make_tree, makepath, require_tcp, rmtree, rsync_argv,
    start_test_daemon, test_fail, write_daemon_conf,
)

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
    for s in socks:
        try:
            s.close()
        except OSError:
            pass

def proxy_handle(cli):
    hdr = b''
    while b'\r\n\r\n' not in hdr and len(hdr) < 4096:
        c = cli.recv(1)
        if not c:
            cli.close()
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
        cli.close()
        return
    cli.sendall(b'HTTP/1.0 200 Connection established\r\n\r\n')
    up = socket.create_connection(('127.0.0.1', DAEMON_PORT))
    relay(cli, up)

def proxy_loop(lsock):
    while True:
        try:
            c, _ = lsock.accept()
        except OSError:
            return
        threading.Thread(target=proxy_handle, args=(c,), daemon=True).start()

lsock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
lsock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
lsock.bind(('127.0.0.1', PROXY_PORT))
lsock.listen(8)
threading.Thread(target=proxy_loop, args=(lsock,), daemon=True).start()

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

lsock.close()
print(f"daemon-http-proxy: CONNECT (no-auth + Basic auth) + 503-deny ok")

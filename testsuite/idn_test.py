#!/usr/bin/env python3
# Verify that rsync converts an IDN (internationalized domain name) host to
# its IDNA A-label (Punycode) form.
#
# Two daemon connection methods carry the host name out of rsync, so both are
# checked:
#   * daemon over a remote shell (what rsync-ssl does): the host is handed to
#     the --rsh helper.
#   * direct daemon socket: observed through a dummy HTTP proxy (RSYNC_PROXY) on
#     loopback, so this part only runs under --use-tcp.
# A plain remote-shell transfer (host:path) is intentionally left alone, since
# that name belongs to the user's ssh.

import os
import shlex
import socket
import subprocess
import sys
import threading

from rsyncfns import (
    RSYNC, SCRATCHDIR, USE_TCP, claim_ports, run_rsync,
    test_fail, test_skipped,
)


if '"IDN": true' not in run_rsync('-VV', check=True, capture_output=True).stdout:
    test_skipped("rsync built without IDN support")


def find_utf8_locale():
    try:
        out = subprocess.check_output(['locale', '-a'], text=True,
                                      stderr=subprocess.DEVNULL)
    except (OSError, subprocess.CalledProcessError):
        return None
    avail = out.split()
    for want in ('C.UTF-8', 'C.utf8', 'en_US.UTF-8', 'en_US.utf8'):
        if want in avail:
            return want
    for loc in avail:
        if loc.lower().replace('-', '').endswith('utf8'):
            return loc
    return None


utf8_locale = find_utf8_locale()
if not utf8_locale:
    test_skipped("no UTF-8 locale available to encode the IDN host")

idn_host = "\u010ci\u010dku.example"
ascii_host = "xn--iku-eqab.example"

env = os.environ.copy()
env['LC_ALL'] = utf8_locale
out_dir = (str(SCRATCHDIR / 'out') + '/').encode()


def run_idn(url, *extra, extra_env=None):
    # A bytes argv keeps the UTF-8 host intact regardless of Python's
    # filesystem encoding.
    e = dict(env)
    if extra_env:
        e.update(extra_env)
    argv = [a.encode() for a in shlex.split(RSYNC)]
    argv += [a.encode() for a in extra]
    argv += [url.encode('utf-8'), out_dir]
    return subprocess.run(argv, capture_output=True, env=e, timeout=30)


# --- daemon over a remote shell (the rsync-ssl mechanism) ------------------
helper = SCRATCHDIR / 'idn-rsh.sh'
helper.write_text('#!/bin/sh\nprintf %s "$1" > "$IDN_RSH_OUT"\nexit 1\n')
helper.chmod(0o755)

hostfile = SCRATCHDIR / 'idn-rsh-host'
if hostfile.exists():
    hostfile.unlink()

run_idn(f"rsync://{idn_host}/module/", f"--rsh={helper}",
        extra_env={'IDN_RSH_OUT': str(hostfile)})

got = hostfile.read_text() if hostfile.exists() else '<helper never ran>'
if got != ascii_host:
    test_fail(f"daemon-over-rsh sent host {got!r}, expected A-label {ascii_host!r}")
print(f"OK: daemon-over-rsh (rsync-ssl style) host sent as {ascii_host}")


# --- direct daemon socket, observed via a dummy proxy -----------------------
if not USE_TCP:
    print("direct-socket proxy check needs --use-tcp; skipping that part")
    sys.exit(0)

PROXY_PORT = 13335
claim_ports(PROXY_PORT)

listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
listener.bind(('127.0.0.1', PROXY_PORT))
listener.listen(1)

captured = {}


def serve_one():
    conn, _ = listener.accept()
    conn.settimeout(5)
    data = b""
    try:
        while b"\r\n\r\n" not in data and len(data) < 65536:
            chunk = conn.recv(8192)
            if not chunk:
                break
            data += chunk
    except socket.timeout:
        pass
    captured['request'] = data
    try:
        conn.sendall(b"HTTP/1.0 403 Forbidden\r\n\r\n")
        conn.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass
    conn.close()


t = threading.Thread(target=serve_one)
t.daemon = True
t.start()

proc = run_idn(f"rsync://{idn_host}:873/whatever/",
               extra_env={'RSYNC_PROXY': f'127.0.0.1:{PROXY_PORT}'})

t.join(timeout=15)
listener.close()

if proc.returncode >= 128:
    sys.stderr.write(proc.stderr.decode('latin1'))
    test_fail(f"rsync killed by signal (status={proc.returncode})")

request = captured.get('request', b'')
if not request:
    test_fail("dummy proxy received no CONNECT request from rsync")

if ascii_host.encode() not in request:
    sys.stderr.write("proxy received: %r\n" % request.split(b"\r\n", 1)[0])
    test_fail(f"expected A-label {ascii_host} in the proxy CONNECT request")

print(f"OK: direct-socket CONNECT host sent as {ascii_host}")

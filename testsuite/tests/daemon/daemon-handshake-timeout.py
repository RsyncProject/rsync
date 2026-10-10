#!/usr/bin/env python3

import select
from pathlib import Path
import socket
import time

from harness.rsync import (
    RSYNC, SCRATCHDIR, makepath, require_tcp, rmtree, start_test_daemon,
    test_fail, under_valgrind, write_daemon_conf,
)

require_tcp("raw stalled clients need a real TCP daemon; run with --use-tcp")

BUILTIN = 60
FAST = 2
SLOP = 8

base = SCRATCHDIR / 'daemon-handshake-timeout'
rmtree(base)
mod = base / 'mod'
makepath(mod)
(mod / 'f').write_text('data\n')
secrets = base / 'secrets'
secrets.write_text('user:password\n')
secrets.chmod(0o600)

def port_of(url):
    return int(url.rsplit(':', 1)[1].rstrip('/'))

def recv_until(sock, marker, deadline):
    data = b''
    while marker not in data:
        left = deadline - time.monotonic()
        if left <= 0:
            test_fail(f"timed out waiting for {marker!r}; received {data!r}")
        sock.settimeout(left)
        try:
            chunk = sock.recv(4096)
        except OSError as e:
            test_fail(f"connection closed waiting for {marker!r}: {e}; got {data!r}")
        if not chunk:
            test_fail(f"EOF waiting for {marker!r}; received {data!r}")
        data += chunk
    return data

def connect(port):
    deadline = time.monotonic() + 10
    left = deadline - time.monotonic()
    try:
        sock = socket.create_connection(('127.0.0.1', port), timeout=left)
    except OSError as e:
        test_fail(f"could not connect to test daemon on port {port}: {e}")
    recv_until(sock, b'\n', deadline)
    return sock

def open_module(port, name):
    sock = connect(port)
    sock.sendall(b'@RSYNCD: 31.0\n' + name.encode() + b'\n')
    got = recv_until(sock, b'\n', time.monotonic() + 10)
    if b'@RSYNCD: OK' not in got:
        test_fail(f"module {name!r} was not accepted: {got!r}")
    return sock

_TIMEOUT_MARKERS = ("daemon handshake timeout", "io timeout after")

_LOG_TAIL_BYTES = 4096

def _log_mark(path):
    if not path:
        return None
    try:
        return Path(path).stat().st_size
    except OSError:
        return 0

def _log_since(path, mark):
    if not path or mark is None:
        return ""
    try:
        with open(path, 'rb') as fh:
            fh.seek(mark)
            raw = fh.read(_LOG_TAIL_BYTES + 1)
    except OSError:
        return ""
    clipped = len(raw) > _LOG_TAIL_BYTES
    text = raw[:_LOG_TAIL_BYTES].decode('utf-8', 'replace')
    text = ''.join(c if c == '\n' or ' ' <= c <= '~' else '.' for c in text)
    return text + ('...[truncated]' if clipped else '')

def _timed_out_since(path, mark):
    text = _log_since(path, mark)
    return any(m in text for m in _TIMEOUT_MARKERS)

def _clock_note(wall, mono):
    if mono is None:
        return ""
    if abs(wall - mono) < 1.0:
        return ""
    return f' [wall={wall:.2f}s monotonic={mono:.2f}s drift={abs(wall - mono):.2f}s]'

def _daemon_log_note(path, mark):
    if not path:
        return ""
    text = _log_since(path, mark).strip()
    if not text:
        return f' [no daemon log output from {path}]'
    return " [daemon log: " + " | ".join(text.splitlines()[-12:]) + "]"

def observe_close(sock, expected, label, *, trickle=None, must_close=True,
                  daemon_log=None):
    start = time.monotonic()
    start_wall = time.time()
    log_mark = _log_mark(daemon_log)
    deadline = start + expected + SLOP
    next_send = start if trickle is not None else deadline
    can_send = trickle is not None
    closed = None
    closed_mono = None

    while True:
        now = time.monotonic()
        if now >= deadline:
            break
        wait = min(deadline, next_send) - now if can_send else deadline - now
        try:
            readable, _, _ = select.select([sock], [], [], max(0, wait))
        except OSError as e:
            test_fail(f"{label}: select failed before closure was observed: {e}")

        if readable:
            try:
                data = sock.recv(65536)
            except OSError:
                closed, closed_mono = time.time() - start_wall, time.monotonic() - start
                break
            if not data:
                closed, closed_mono = time.time() - start_wall, time.monotonic() - start
                break

        now = time.monotonic()
        if can_send and now >= next_send:
            try:
                sock.sendall(trickle)
            except OSError:
                can_send = False
            next_send = now + 0.20

    if closed is None:
        if must_close:
            test_fail(f"{label}: connection stayed open past {expected + SLOP:.1f}s"
                      f"{_clock_note(time.time() - start_wall, time.monotonic() - start)}"
                      f"{_daemon_log_note(daemon_log, log_mark)}")
        return None

    margin = 2.5 if under_valgrind() else 1.25
    minimum = max(0.5, expected - margin)
    if closed < minimum:
        test_fail(f"{label}: connection closed after {closed:.2f}s, before the "
                  f"expected timeout window ({minimum:.2f}s); this was not the "
                  f"deadline.{_clock_note(closed, closed_mono)}"
                  f"{_daemon_log_note(daemon_log, log_mark)}")
    if closed_mono is not None and closed_mono < minimum \
       and not _timed_out_since(daemon_log, log_mark):
        test_fail(f"{label}: early monotonic close {closed_mono:.2f}s without a timeout log"
                  f"{_clock_note(closed, closed_mono)}"
                  f"{_daemon_log_note(daemon_log, log_mark)}")
    if not must_close:
        test_fail(f"{label}: connection unexpectedly closed after {closed:.2f}s"
                  f"{_clock_note(closed, closed_mono)}"
                  f"{_daemon_log_note(daemon_log, log_mark)}")
    return closed

def stall_greeting(port, expected, label, *, daemon_log=None):
    sock = connect(port)
    try:
        sock.sendall(b'@RSYNCD: 31.0')
        return observe_close(sock, expected, label, trickle=b'x',
                             daemon_log=daemon_log)
    finally:
        sock.close()

def transfer_args(client_timeout=None):
    args = [b'--server', b'--sender', b'-logDtpre.iLsfxCIvu']
    if client_timeout is not None:
        args.append(f'--timeout={client_timeout}'.encode())
    args += [b'.', b'/']
    return b'\0'.join(args) + b'\0\0'

def stalled_transfer(port, module, expected, label, *, client_timeout=None,
                     must_close=True, daemon_log=None):
    sock = open_module(port, module)
    try:
        sock.sendall(transfer_args(client_timeout))
        return observe_close(sock, expected, label, must_close=must_close,
                             daemon_log=daemon_log)
    finally:
        sock.close()

conf_fast = write_daemon_conf(
    [
        ('zero', {'path': str(mod), 'timeout': '0', 'read only': 'yes'}),
        ('long', {'path': str(mod), 'timeout': '5', 'read only': 'yes'}),
        ('clientlow', {'path': str(mod), 'timeout': '8', 'read only': 'yes'}),
        ('modulelow', {'path': str(mod), 'timeout': '3', 'read only': 'yes'}),
    ],
    globals={
        'timeout': str(FAST),
        'pid file': str(base / 'fast.pid'),
        'log file': str(base / 'fast.log'),
    },
    name='handshake-timeout-fast.conf',
)
port_fast = port_of(start_test_daemon(conf_fast, 12987, rsync_cmd=RSYNC))
global_took = stall_greeting(port_fast, FAST, 'global handshake timeout',
                              daemon_log=base / 'fast.log')

conf_args = write_daemon_conf(
    [
        ('args', {'path': str(mod), 'timeout': str(FAST), 'read only': 'yes'}),
        ('auth', {
            'path': str(mod),
            'timeout': str(FAST),
            'read only': 'yes',
            'auth users': 'user',
            'secrets file': str(secrets),
        }),
    ],
    globals={
        'timeout': '20',
        'pid file': str(base / 'args.pid'),
        'log file': str(base / 'args.log'),
    },
    name='handshake-timeout-args.conf',
)
port_args = port_of(start_test_daemon(conf_args, 12988, rsync_cmd=RSYNC))

s = connect(port_args)
try:
    s.sendall(b'@RSYNCD: 31.0\nauth\n')
    auth_reply = recv_until(s, b'\n', time.monotonic() + 10)
    if b'@RSYNCD: AUTHREQD ' not in auth_reply:
        test_fail(f"authenticated module did not issue a challenge: {auth_reply!r}")
    auth_took = observe_close(s, FAST, 'unauthenticated claimed slot', trickle=b'x',
                              daemon_log=base / 'args.log')
finally:
    s.close()

s = open_module(port_args, 'args')
try:
    first_took = observe_close(s, FAST, 'anonymous first argument read', trickle=b'x',
                               daemon_log=base / 'args.log')
finally:
    s.close()

s = open_module(port_args, 'args')
try:
    s.sendall(b'--server\0--sender\0-s\0\0')
    second_took = observe_close(s, FAST, 'anonymous secluded-args read', trickle=b'x',
                                daemon_log=base / 'args.log')
finally:
    s.close()

long_took = stalled_transfer(port_fast, 'long', 5,
                             'module transfer timeout above global',
                             daemon_log=base / 'fast.log')
client_took = stalled_transfer(port_fast, 'clientlow', 3,
                               'client timeout below module', client_timeout=3,
                               daemon_log=base / 'fast.log')
module_took = stalled_transfer(port_fast, 'modulelow', 3,
                               'module timeout below client', client_timeout=8,
                               daemon_log=base / 'fast.log')
stalled_transfer(port_fast, 'zero', 4,
                 'explicit module timeout=0 transfer', must_close=False,
                 daemon_log=base / 'fast.log')

conf_default = write_daemon_conf(
    [('default', {'path': str(mod), 'read only': 'yes'})],
    globals={
        'pid file': str(base / 'default.pid'),
        'log file': str(base / 'default.log'),
    },
    name='handshake-timeout-default.conf',
)
port_default = port_of(start_test_daemon(conf_default, 12989, rsync_cmd=RSYNC))
builtin_took = stall_greeting(port_default, BUILTIN, 'built-in handshake timeout',
                               daemon_log=base / 'default.log')

print(
    'daemon handshake deadlines: '
    f'global={global_took:.1f}s, auth={auth_took:.1f}s, '
    f'anonymous args={first_took:.1f}/{second_took:.1f}s, '
    f'transfer module/client limits={long_took:.1f}/{client_took:.1f}/{module_took:.1f}s, '
    f'built-in={builtin_took:.1f}s; explicit module timeout=0 stayed open'
)

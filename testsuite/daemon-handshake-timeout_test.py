#!/usr/bin/env python3
"""Exercise the absolute deadline on peer-controlled daemon handshake reads.

The deadline is separate from the transfer's idle timeout.  These probes cover
the pre-module phase, anonymous modules, both argument reads, module-local
policy, trickling input, and transfer timeout combinations.  Raw sockets are
intentional: a normal rsync client never leaves a handshake line unfinished.
"""

import select
import socket
import time

from rsyncfns import (
    RSYNC, SCRATCHDIR, makepath, require_tcp, rmtree, start_test_daemon,
    test_fail, write_daemon_conf,
)

require_tcp("raw stalled clients need a real TCP daemon; run with --use-tcp")

BUILTIN = 60                 # DAEMON_HANDSHAKE_TIMEOUT in clientserver.c
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
    """Read through marker using an absolute monotonic deadline."""
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


def observe_close(sock, expected, label, *, trickle=None, must_close=True):
    """Observe EOF/reset by recv() before an absolute deadline.

    A failed send is only a hint: keep receiving until EOF or a terminal read
    error.  This avoids the clean-FIN race where a later send can still succeed
    on Cygwin and some TCP stacks.
    """
    start = time.monotonic()
    deadline = start + expected + SLOP
    next_send = start if trickle is not None else deadline
    can_send = trickle is not None
    closed = None

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
                closed = time.monotonic() - start
                break
            if not data:
                closed = time.monotonic() - start
                break

        now = time.monotonic()
        if can_send and now >= next_send:
            try:
                sock.sendall(trickle)
            except OSError:
                # Do not infer closure from the write side.  FIN/reset/abort is
                # confirmed by the recv path above on a subsequent iteration.
                can_send = False
            next_send = now + 0.20

    if closed is None:
        if must_close:
            test_fail(f"{label}: connection stayed open past {expected + SLOP:.1f}s")
        return None

    # A refusal, parse error, or unrelated daemon failure must not pass as a
    # timeout.  Leave margin for one-second time() granularity in the daemon.
    minimum = max(0.5, expected - 1.25)
    if closed < minimum:
        test_fail(f"{label}: connection closed after {closed:.2f}s, before the "
                  f"expected timeout window ({minimum:.2f}s); this was not the deadline")
    if not must_close:
        test_fail(f"{label}: connection unexpectedly closed after {closed:.2f}s")
    return closed


def stall_greeting(port, expected, label):
    sock = connect(port)
    try:
        sock.sendall(b'@RSYNCD: 31.0')       # deliberately no newline
        return observe_close(sock, expected, label, trickle=b'x')
    finally:
        sock.close()


def transfer_args(client_timeout=None):
    args = [b'--server', b'--sender', b'-logDtpre.iLsfxCIvu']
    if client_timeout is not None:
        args.append(f'--timeout={client_timeout}'.encode())
    args += [b'.', b'/']
    return b'\0'.join(args) + b'\0\0'


def stalled_transfer(port, module, expected, label, *, client_timeout=None,
                     must_close=True):
    sock = open_module(port, module)
    try:
        sock.sendall(transfer_args(client_timeout))
        return observe_close(sock, expected, label, must_close=must_close)
    finally:
        sock.close()


# A positive global value governs input before a module is named.
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
global_took = stall_greeting(port_fast, FAST, 'global handshake timeout')


# Once an anonymous module is named, its local value must cover the claimed
# slot through the first and the secluded-args read.  Continuous bytes prove
# this is an absolute deadline, not an idle timeout.
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
    auth_took = observe_close(s, FAST, 'unauthenticated claimed slot', trickle=b'x')
finally:
    s.close()

s = open_module(port_args, 'args')
try:
    first_took = observe_close(s, FAST, 'anonymous first argument read', trickle=b'x')
finally:
    s.close()

s = open_module(port_args, 'args')
try:
    # The first list selects secluded args and terminates normally.  The second
    # read gets a never-terminated NUL string and must share the original bound.
    s.sendall(b'--server\0--sender\0-s\0\0')
    second_took = observe_close(s, FAST, 'anonymous secluded-args read', trickle=b'x')
finally:
    s.close()


# Transfer policy is independent: module values may extend the global value,
# a client may shorten a module, a module may shorten a client, and an explicit
# module timeout=0 means no transfer timeout at all.
long_took = stalled_transfer(port_fast, 'long', 5,
                             'module transfer timeout above global')
client_took = stalled_transfer(port_fast, 'clientlow', 3,
                               'client timeout below module', client_timeout=3)
module_took = stalled_transfer(port_fast, 'modulelow', 3,
                               'module timeout below client', client_timeout=8)
stalled_transfer(port_fast, 'zero', 4,
                 'explicit module timeout=0 transfer', must_close=False)


# With no configured timeout, the named and documented built-in bound applies.
conf_default = write_daemon_conf(
    [('default', {'path': str(mod), 'read only': 'yes'})],
    globals={
        'pid file': str(base / 'default.pid'),
        'log file': str(base / 'default.log'),
    },
    name='handshake-timeout-default.conf',
)
port_default = port_of(start_test_daemon(conf_default, 12989, rsync_cmd=RSYNC))
builtin_took = stall_greeting(port_default, BUILTIN, 'built-in handshake timeout')

print(
    'daemon handshake deadlines: '
    f'global={global_took:.1f}s, auth={auth_took:.1f}s, '
    f'anonymous args={first_took:.1f}/{second_took:.1f}s, '
    f'transfer module/client limits={long_took:.1f}/{client_took:.1f}/{module_took:.1f}s, '
    f'built-in={builtin_took:.1f}s; explicit module timeout=0 stayed open'
)

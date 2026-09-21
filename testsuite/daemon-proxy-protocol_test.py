#!/usr/bin/env python3
# Exercise the daemon's HAProxy PROXY-protocol parser (clientname.c
# read_proxy_protocol_header), the trusted-peer gate
# (clientserver.c proxy_peer_allowed -> access.c allow_proxy_protocol_peer),
# and that the proxied source address is what hosts-allow checks against.
#
# Needs a real TCP socket: read_proxy_protocol_header() runs only when
# lp_proxy_protocol() is set, and it reads from the socket BEFORE the
# @RSYNCD greeting -- there is no pipe-transport equivalent.

import socket
import struct

from rsyncfns import (
    SCRATCHDIR, claim_ports, makepath, require_tcp, start_rsyncd, test_fail,
    write_daemon_conf,
)

PORT_OK = 19873          # proxy protocol hosts = 127.0.0.0/8 (we match)
PORT_NOHOSTS = 19874     # proxy protocol = yes but NO proxy protocol hosts

require_tcp("PROXY-protocol header is read from a real TCP socket; run with --use-tcp")
claim_ports(PORT_OK, PORT_NOHOSTS)

base = SCRATCHDIR / 'proxy-protocol'
makepath(base / 'mod')


# Daemon 1: trusted proxy at 127.0.0.0/8.  Module only allows the *proxied*
# 10.0.0.0/8 (and an IPv6 ULA), so a successful module-list proves the daemon
# is checking the proxied addr, not the socket peer (127.0.0.1).
conf_ok = write_daemon_conf(
    [('mod', {
        'path': str(base / 'mod'),
        'read only': 'yes',
        'use chroot': 'no',
        'hosts allow': '10.0.0.0/8, fd00::/8',
    })],
    globals={
        'proxy protocol': 'yes',
        'proxy protocol hosts': '127.0.0.0/8',
        # The proxied addrs are not in DNS -- skip name lookup so the test
        # does not block on a slow reverse-DNS timeout.
        'reverse lookup': 'no',
        # Override the write_daemon_conf default (localhost 127/8) -- with the
        # proxy header in play we want hosts-allow to be per-module only.
        'hosts allow': '',
    },
    name='proxyproto.conf',
)
start_rsyncd(conf_ok, PORT_OK)

# Daemon 2: proxy protocol = yes but NO `proxy protocol hosts` -> every peer
# is untrusted (allow_proxy_protocol_peer returns 0 on empty list), the
# connection is dropped before the header is even read.
conf_nohosts = write_daemon_conf(
    [('mod', {'path': str(base / 'mod'), 'read only': 'yes', 'use chroot': 'no'})],
    globals={
        'proxy protocol': 'yes',
        'reverse lookup': 'no',
        'hosts allow': '',
        # Second daemon in one test -> distinct pid/log so we don't collide
        # with daemon 1's lock.
        'pid file': str(SCRATCHDIR / 'rsyncd-nohosts.pid'),
        'log file': str(SCRATCHDIR / 'rsyncd-nohosts.log'),
    },
    name='proxyproto-nohosts.conf',
)
start_rsyncd(conf_nohosts, PORT_NOHOSTS)


# --- PROXY header encoders (mirror clientname.c constants) ------------------

V2_SIG = b'\r\n\r\n\x00\r\nQUIT\n'    # 12 bytes
CMD_LOCAL, CMD_PROXY = 0, 1
FAM_TCPv4, FAM_TCPv6 = 0x11, 0x21


def v1(line):
    return (b'PROXY ' + line.encode('ascii') + b'\r\n')


def v2(cmd, fam, addr):
    return (V2_SIG
            + bytes([(2 << 4) | cmd, fam])
            + struct.pack('>H', len(addr))
            + addr)


def v2_ip4(src, dst='127.0.0.1', sport=40000, dport=PORT_OK):
    a = (socket.inet_pton(socket.AF_INET, src)
         + socket.inet_pton(socket.AF_INET, dst)
         + struct.pack('>HH', sport, dport))
    return v2(CMD_PROXY, FAM_TCPv4, a)


def v2_ip6(src, dst='::1', sport=40000, dport=PORT_OK):
    a = (socket.inet_pton(socket.AF_INET6, src)
         + socket.inet_pton(socket.AF_INET6, dst)
         + struct.pack('>HH', sport, dport))
    return v2(CMD_PROXY, FAM_TCPv6, a)


# --- driver: send proxy header then a minimal @RSYNCD module-list -----------

def probe(port, hdr, label, *, want):
    """Connect, send `hdr`, then attempt an @RSYNCD module-list of [mod].

    `want` is one of:
        'ok'     -- daemon answers @RSYNCD: OK (proxied IP passed hosts-allow)
        'denied' -- daemon answers @ERROR: access denied (proxied IP rejected
                    by per-module hosts-allow)
        'drop'   -- daemon closes the socket without an @RSYNCD greeting
                    (proxy header rejected or untrusted peer)
    """
    s = socket.create_connection(('127.0.0.1', port), timeout=10)
    s.settimeout(10)
    out = b''
    try:
        if hdr:
            s.sendall(hdr)
        # @RSYNCD client greeting + module name + a single sender-side
        # list-only request.  Protocol 30, no capabilities, no auth.
        s.sendall(b'@RSYNCD: 30.0\nmod\n')
        # Slurp everything the daemon writes before it closes.
        try:
            s.shutdown(socket.SHUT_WR)
        except OSError:
            pass
        while True:
            try:
                chunk = s.recv(4096)
            except OSError:
                break
            if not chunk:
                break
            out += chunk
    except OSError:
        # A 'drop' daemon closes the connection, which can surface as
        # EPIPE/ECONNRESET on our sendall() before we ever read -- a timing
        # race seen on some CI runners.  Leave `out` empty and let the
        # want-check below decide: for want='drop' the absent greeting is the
        # expected outcome; for want='ok'/'denied' it still fails correctly.
        pass
    finally:
        s.close()

    got_greeting = out.startswith(b'@RSYNCD:')
    if want == 'drop':
        if got_greeting:
            test_fail(f"{label}: expected drop, got greeting: {out!r}")
        return
    if not got_greeting:
        test_fail(f"{label}: expected @RSYNCD greeting, got: {out!r}")
    if want == 'denied':
        if b'@ERROR' not in out or b'access denied' not in out:
            test_fail(f"{label}: expected access-denied, got: {out!r}")
        return
    if want == 'ok':
        # After greeting + module name the daemon answers @RSYNCD: OK then
        # waits for client args -- but we sent SHUT_WR so it sees EOF on the
        # args read and closes.  Seeing OK (not @ERROR) is the success signal.
        if b'@ERROR' in out:
            test_fail(f"{label}: expected OK, got error: {out!r}")
        if b'@RSYNCD: OK' not in out:
            test_fail(f"{label}: expected @RSYNCD: OK, got: {out!r}")
        return
    test_fail(f"{label}: bad want={want!r}")


# === V1 text header =========================================================

# Proxied src in 10.0.0.0/8 -> module hosts-allow matches.
probe(PORT_OK, v1('TCP4 10.1.2.3 127.0.0.1 40000 873'), 'v1 TCP4 allow', want='ok')

# Proxied src outside 10.0.0.0/8 -> module hosts-allow rejects (proves the
# daemon checked the *proxied* addr, not the socket peer 127.0.0.1).
probe(PORT_OK, v1('TCP4 192.168.1.1 127.0.0.1 40000 873'), 'v1 TCP4 deny', want='denied')

# IPv6 source in fd00::/8 -> matches the ULA hosts-allow entry.
probe(PORT_OK, v1('TCP6 fd00::1234 ::1 40000 873'), 'v1 TCP6 allow', want='ok')

# UNKNOWN -> parser returns 1 with ipaddr_buf unset, so the daemon falls back
# to the socket peer (127.0.0.1), which is NOT in 10.0.0.0/8 -> denied.
probe(PORT_OK, v1('UNKNOWN'), 'v1 UNKNOWN', want='denied')

# Malformed v1: missing dst -> parser returns 0 -> drop.
probe(PORT_OK, v1('TCP4 10.1.2.3'), 'v1 short', want='drop')

# Malformed v1: bad family token.
probe(PORT_OK, v1('TCP9 10.1.2.3 127.0.0.1 40000 873'), 'v1 bad-fam', want='drop')

# Malformed v1: non-numeric port.
probe(PORT_OK, v1('TCP4 10.1.2.3 127.0.0.1 abc 873'), 'v1 bad-port', want='drop')

# Malformed v1: line longer than the 108-byte buffer with no \n -> drop.
probe(PORT_OK, b'PROXY TCP4 ' + b'1' * 200, 'v1 overlong', want='drop')

# Neither v2 sig nor "PROXY" prefix -> drop.
probe(PORT_OK, b'GET / HTTP/1.0\r\n\r\n', 'no proxy hdr', want='drop')


# === V2 binary header =======================================================

probe(PORT_OK, v2_ip4('10.9.8.7'), 'v2 TCPv4 allow', want='ok')
probe(PORT_OK, v2_ip4('172.16.0.1'), 'v2 TCPv4 deny', want='denied')
probe(PORT_OK, v2_ip6('fd00::abcd'), 'v2 TCPv6 allow', want='ok')

# CMD_LOCAL: parser returns 1, ipaddr_buf unset -> socket peer 127.0.0.1
# checked against hosts-allow -> denied.
probe(PORT_OK, v2(CMD_LOCAL, 0, b''), 'v2 LOCAL', want='denied')

# Unsupported family with CMD_PROXY -> "ignore proxy data, accept" branch.
probe(PORT_OK, v2(CMD_PROXY, 0x31, b'\0' * 4), 'v2 unsupp-fam', want='denied')

# Bad version nibble -> drop.
probe(PORT_OK, V2_SIG + bytes([(3 << 4) | CMD_PROXY, FAM_TCPv4]) + b'\x00\x0c'
      + b'\0' * 12, 'v2 bad-ver', want='drop')

# Declared size larger than the union -> drop.
probe(PORT_OK, V2_SIG + bytes([(2 << 4) | CMD_PROXY, FAM_TCPv4]) + b'\x10\x00'
      + b'\0' * 12, 'v2 oversize', want='drop')

# TCPv4 with size != 12 -> drop.
probe(PORT_OK, v2(CMD_PROXY, FAM_TCPv4, b'\0' * 8), 'v2 ip4 bad-len', want='drop')

# Unknown cmd -> drop.
probe(PORT_OK, v2(7, FAM_TCPv4, b'\0' * 12), 'v2 bad-cmd', want='drop')


# === Untrusted-peer gate (no `proxy protocol hosts`) ========================

# allow_proxy_protocol_peer() returns 0 on an empty/NULL list; the daemon
# drops the connection BEFORE reading any proxy header.
probe(PORT_NOHOSTS, v2_ip4('10.1.2.3'), 'untrusted v2', want='drop')
probe(PORT_NOHOSTS, v1('TCP4 10.1.2.3 127.0.0.1 40000 873'), 'untrusted v1', want='drop')
probe(PORT_NOHOSTS, b'', 'untrusted empty', want='drop')

print("PASS daemon-proxy-protocol: 22 cases")

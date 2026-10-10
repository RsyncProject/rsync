#!/usr/bin/env python3

import socket
import struct

from harness.daemon_config import write_daemon_conf
from harness.rsync import SCRATCHDIR, claim_ports, makepath, require_tcp, start_rsyncd, test_fail

PORT_OK = 19873
PORT_NOHOSTS = 19874

require_tcp("PROXY-protocol header is read from a real TCP socket; run with --use-tcp")
claim_ports(PORT_OK, PORT_NOHOSTS)

base = SCRATCHDIR / 'proxy-protocol'
makepath(base / 'mod')

conf_ok = write_daemon_conf(
    [('mod', {
        'path': str(base / 'mod'),
        'read only': 'yes',
        'use chroot': 'no',
        'hosts allow': '10.0.0.0/8, fd00::/8',
    })],
    global_options={
        'proxy protocol': 'yes',
        'proxy protocol hosts': '127.0.0.0/8',
        'reverse lookup': 'no',
        'hosts allow': '',
    },
    name='proxyproto.conf',
)
start_rsyncd(conf_ok, PORT_OK)

conf_nohosts = write_daemon_conf(
    [('mod', {'path': str(base / 'mod'), 'read only': 'yes', 'use chroot': 'no'})],
    global_options={
        'proxy protocol': 'yes',
        'reverse lookup': 'no',
        'hosts allow': '',
        'pid file': str(SCRATCHDIR / 'rsyncd-nohosts.pid'),
        'log file': str(SCRATCHDIR / 'rsyncd-nohosts.log'),
    },
    name='proxyproto-nohosts.conf',
)
start_rsyncd(conf_nohosts, PORT_NOHOSTS)

V2_SIG = b'\r\n\r\n\x00\r\nQUIT\n'
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

def probe(port, hdr, label, *, want):
    s = socket.create_connection(('127.0.0.1', port), timeout=10)
    s.settimeout(10)
    out = b''
    try:
        if hdr:
            s.sendall(hdr)
        s.sendall(b'@RSYNCD: 30.0\nmod\n')
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
        if b'@ERROR' in out:
            test_fail(f"{label}: expected OK, got error: {out!r}")
        if b'@RSYNCD: OK' not in out:
            test_fail(f"{label}: expected @RSYNCD: OK, got: {out!r}")
        return
    test_fail(f"{label}: bad want={want!r}")

probe(PORT_OK, v1('TCP4 10.1.2.3 127.0.0.1 40000 873'), 'v1 TCP4 allow', want='ok')

probe(PORT_OK, v1('TCP4 192.168.1.1 127.0.0.1 40000 873'), 'v1 TCP4 deny', want='denied')

probe(PORT_OK, v1('TCP6 fd00::1234 ::1 40000 873'), 'v1 TCP6 allow', want='ok')

probe(PORT_OK, v1('UNKNOWN'), 'v1 UNKNOWN', want='denied')

probe(PORT_OK, v1('TCP4 10.1.2.3'), 'v1 short', want='drop')

probe(PORT_OK, v1('TCP9 10.1.2.3 127.0.0.1 40000 873'), 'v1 bad-fam', want='drop')

probe(PORT_OK, v1('TCP4 10.1.2.3 127.0.0.1 abc 873'), 'v1 bad-port', want='drop')

probe(PORT_OK, b'PROXY TCP4 ' + b'1' * 200, 'v1 overlong', want='drop')

probe(PORT_OK, b'GET / HTTP/1.0\r\n\r\n', 'no proxy hdr', want='drop')

probe(PORT_OK, v2_ip4('10.9.8.7'), 'v2 TCPv4 allow', want='ok')
probe(PORT_OK, v2_ip4('172.16.0.1'), 'v2 TCPv4 deny', want='denied')
probe(PORT_OK, v2_ip6('fd00::abcd'), 'v2 TCPv6 allow', want='ok')

probe(PORT_OK, v2(CMD_LOCAL, 0, b''), 'v2 LOCAL', want='denied')

probe(PORT_OK, v2(CMD_PROXY, 0x31, b'\0' * 4), 'v2 unsupp-fam', want='denied')

probe(PORT_OK, V2_SIG + bytes([(3 << 4) | CMD_PROXY, FAM_TCPv4]) + b'\x00\x0c'
      + b'\0' * 12, 'v2 bad-ver', want='drop')

probe(PORT_OK, V2_SIG + bytes([(2 << 4) | CMD_PROXY, FAM_TCPv4]) + b'\x10\x00'
      + b'\0' * 12, 'v2 oversize', want='drop')

probe(PORT_OK, v2(CMD_PROXY, FAM_TCPv4, b'\0' * 8), 'v2 ip4 bad-len', want='drop')

probe(PORT_OK, v2(7, FAM_TCPv4, b'\0' * 12), 'v2 bad-cmd', want='drop')

probe(PORT_NOHOSTS, v2_ip4('10.1.2.3'), 'untrusted v2', want='drop')
probe(PORT_NOHOSTS, v1('TCP4 10.1.2.3 127.0.0.1 40000 873'), 'untrusted v1', want='drop')
probe(PORT_NOHOSTS, b'', 'untrusted empty', want='drop')

print("PASS daemon-proxy-protocol: 22 cases")

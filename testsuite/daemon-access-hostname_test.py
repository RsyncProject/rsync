#!/usr/bin/env python3
"""Daemon forward-DNS hostname rules against IPv4 and IPv6 peers."""

import socket
import subprocess

from rsyncfns import (
    FROMDIR, SCRATCHDIR, RSYNC,
    make_tree, require_tcp, rmtree, rsync_argv, start_test_daemon,
    test_fail, test_skipped,
)

IPV4_PORT = 12895
IPV6_PORT = 12897
require_tcp("hostname access matching needs a real TCP peer")

try:
    for family, address in ((socket.AF_INET, '127.0.0.1'), (socket.AF_INET6, '::1')):
        with socket.socket(family, socket.SOCK_STREAM) as probe:
            probe.bind((address, 0))
except OSError:
    test_skipped("IPv4 or IPv6 loopback is unavailable")


def loopback_name(family, address):
    for candidate in ('localhost', 'ip6-localhost', 'ip6-loopback', socket.gethostname()):
        try:
            answers = socket.getaddrinfo(candidate, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
        except socket.gaierror:
            continue
        if any(item[0] == family and item[4][0] == address for item in answers):
            return candidate
    test_skipped(f"no local hostname resolves to {address}")


host4 = loopback_name(socket.AF_INET, '127.0.0.1')
host6 = loopback_name(socket.AF_INET6, '::1')

src = FROMDIR
rmtree(src)
make_tree(src, depth=2)


def start(family, address, host, port):
    conf = SCRATCHDIR / f'access-hostname-{family}.conf'
    conf.write_text(
        f"pid file = {SCRATCHDIR}/rsyncd-{family}.pid\n"
        "use chroot = no\n"
        "reverse lookup = no\n"
        "forward lookup = yes\n"
        f"log file = {SCRATCHDIR}/{'rsyncd.log' if family == 'ipv6' else 'rsyncd-ipv4.log'}\n"
        f"\n[plain]\n\tpath = {src}\n\tread only = yes\n"
        f"\n[allow-host]\n\tpath = {src}\n\tread only = yes\n\thosts allow = {host}\n"
        f"\n[deny-host]\n\tpath = {src}\n\tread only = yes\n\thosts deny = {host}\n"
        f"\n[allow-unresolved]\n\tpath = {src}\n\tread only = yes\n\thosts allow = nope.nonexistent.invalid\n"
        f"\n[deny-unresolved]\n\tpath = {src}\n\tread only = yes\n\thosts deny = nope.nonexistent.invalid\n"
        f"\n[forward-off]\n\tpath = {src}\n\tread only = yes\n\tforward lookup = no\n\thosts allow = {host}\n"
    )
    return start_test_daemon(conf, port, rsync_cmd=RSYNC, address=address)


urls = (
    ('IPv4', start('ipv4', '127.0.0.1', host4, IPV4_PORT)),
    ('IPv6', start('ipv6', '::1', host6, IPV6_PORT)),
)


def connect(url, module):
    return subprocess.run(
        rsync_argv('-r', f'{url}{module}/'),
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
    ).returncode


for family, url in urls:
    for module in ('plain', 'allow-host'):
        if connect(url, module) != 0:
            test_fail(f"{family} connection to {module} should be allowed")

    for module in ('deny-host', 'allow-unresolved', 'deny-unresolved', 'forward-off'):
        if connect(url, module) == 0:
            test_fail(f"{family} connection to {module} should be denied")

print("daemon-access-hostname: IPv4 and IPv6 hostname rules verified")

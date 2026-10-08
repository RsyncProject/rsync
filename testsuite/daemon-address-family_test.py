#!/usr/bin/env python3
"""Exercise explicit IPv4 and IPv6 daemon connections."""

import socket

from rsyncfns import (
    FROMDIR, RSYNC, SCRATCHDIR, claim_ports, make_tree, rmtree, run_rsync,
    start_rsyncd, write_daemon_conf,
)


PORT4 = 13020
PORT6 = 13021
claim_ports(PORT4, PORT6)

rmtree(FROMDIR)
make_tree(FROMDIR, depth=1)
modules = [('module', {'path': str(FROMDIR), 'read only': 'yes'})]
conf4 = write_daemon_conf(
    modules,
    globals={
        'pid file': str(SCRATCHDIR / 'rsyncd-v4.pid'),
        'log file': str(SCRATCHDIR / 'rsyncd-v4.log'),
        'hosts allow': '127.0.0.0/8',
    },
    name='address-family-v4.conf',
)
conf6 = write_daemon_conf(
    modules,
    globals={
        'pid file': str(SCRATCHDIR / 'rsyncd-v6.pid'),
        'log file': str(SCRATCHDIR / 'rsyncd-v6.log'),
        'hosts allow': '::1',
    },
    name='address-family-v6.conf',
)

start_rsyncd(conf4, PORT4, rsync_cmd=RSYNC, address='127.0.0.1')
run_rsync('-4', '--list-only', f'rsync://127.0.0.1:{PORT4}/module/')

ipv6_tested = False
if socket.has_ipv6:
    try:
        with socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as probe:
            probe.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
            probe.bind(('::1', 0))
    except OSError as error:
        print(f'IPv6 loopback is unavailable: {error}')
    else:
        start_rsyncd(conf6, PORT6, rsync_cmd=RSYNC, address='::1')
        run_rsync('-6', '--list-only', f'rsync://[::1]:{PORT6}/module/')
        ipv6_tested = True

print('explicit IPv4 daemon connection succeeded' +
      ('; explicit IPv6 daemon connection succeeded' if ipv6_tested else ''))

#!/usr/bin/env python3

import socket

from harness.daemon_config import write_daemon_conf
from harness.daemon import claim_ports, require_tcp, start_rsyncd
from harness.environment import FROMDIR, RSYNC, SCRATCHDIR
from harness.filesystem import make_tree, rmtree
from harness.rsync import run_rsync
from harness import metadata

metadata(features={'tcp'}, transports={'tcp'})

PORT4 = 13020
PORT6 = 13021
claim_ports(PORT4, PORT6)
require_tcp('address-family checks require real TCP listeners')

rmtree(FROMDIR)
make_tree(FROMDIR, depth=1)
modules = [('module', {'path': str(FROMDIR), 'read only': 'yes'})]
conf4 = write_daemon_conf(
    modules,
    global_options={
        'pid file': str(SCRATCHDIR / 'rsyncd-v4.pid'),
        'log file': str(SCRATCHDIR / 'rsyncd-v4.log'),
        'hosts allow': '127.0.0.0/8',
    },
    name='address-family-v4.conf',
)
conf6 = write_daemon_conf(
    modules,
    global_options={
        'pid file': str(SCRATCHDIR / 'rsyncd-v6.pid'),
        'log file': str(SCRATCHDIR / 'rsyncd-v6.log'),
        'hosts allow': '::1',
    },
    name='address-family-v6.conf',
)

# The options prefer a family; they do not require opposite-family rejection.
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

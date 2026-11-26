#!/usr/bin/env python3

import socket

from harness.daemon_config import write_daemon_conf
from harness.daemon import claim_free_port, start_rsyncd
from harness.environment import RSYNC, SCRATCHDIR
from harness.filesystem import rmtree
from harness.results import test_skipped
from harness import metadata

metadata(features={'unix_socketpair'}, transports={'pipe', 'tcp'})

if not hasattr(socket, 'AF_UNIX') or not hasattr(socket, 'socketpair'):
    test_skipped('Unix-domain socket pairs are unavailable', capability='unix_socketpair')

base = SCRATCHDIR / 'daemon-stdin-local-socket'
rmtree(base)
module = base / 'module'
module.mkdir(parents=True)
conf = write_daemon_conf([
    ('module', {'path': str(module), 'read only': 'yes'}),
])
port = claim_free_port(12979)

try:
    parent, child = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
except (OSError, ValueError) as e:
    test_skipped(f'Unix-domain socket pairs are unavailable: {e}', capability='unix_socketpair')
try:
    start_rsyncd(conf, port, rsync_cmd=RSYNC, stdin=child)
finally:
    child.close()
    parent.close()

print('daemon ignores a local stdin socket when selecting inetd mode')

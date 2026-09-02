#!/usr/bin/env python3
"""A local socket on stdin must not make a standalone daemon enter inetd mode."""

import socket

from rsyncfns import (
    RSYNC, SCRATCHDIR, claim_free_port, rmtree, start_rsyncd,
    test_skipped, write_daemon_conf,
)

if not hasattr(socket, 'AF_UNIX') or not hasattr(socket, 'socketpair'):
    test_skipped('Unix-domain socket pairs are unavailable')

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
except OSError as e:
    test_skipped(f'Unix-domain socket pairs are unavailable: {e}')
try:
    # ADB shell without a PTY similarly presents a local socket as fd 0.
    # The daemon must ignore that process-I/O transport and listen normally.
    start_rsyncd(conf, port, rsync_cmd=RSYNC, stdin=child)
finally:
    child.close()
    parent.close()

print('daemon ignores a local stdin socket when selecting inetd mode')

#!/usr/bin/env python3
"""Daemon --copy-links must follow a safe parent-relative symlink target."""

import os

from rsyncfns import (
    SCRATCHDIR, forced_protocol, makepath, rmtree, test_fail, test_skipped,
    write_daemon_conf,
)
from stdio_daemon import finish_stdio_daemon, start_stdio_daemon

# The hand-rolled client below speaks protocol 30 and greets with it, so a run
# that pins the daemon lower cannot complete the handshake and just times out.
# The behaviour under test is not protocol-specific -- the sibling
# daemon-copylinks-parent-escape drives the same paths with the real client at
# whatever protocol the run selects.
_proto = forced_protocol()
if _proto is not None and _proto < 30:
    test_skipped(f'the stdio_daemon client speaks protocol 30 (forced {_proto})')

PAYLOAD = 'safe-parent-relative-target\n'

base = SCRATCHDIR / 'daemon-copylinks-parent'
module = base / 'module'
dest = base / 'dest'
rmtree(base)
makepath(module / 'sub', dest)
(module / 'target').write_text(PAYLOAD)
(module / 'sub' / 'control').write_text('control\n')
os.symlink('../target', module / 'sub' / 'parent-link')

conf = write_daemon_conf([
    ('m', {'path': module, 'read only': 'yes', 'hosts allow': '*'}),
])
client, daemon = start_stdio_daemon(conf)
failure = None
try:
    client.handshake(
        'm', ['--server', '--sender', '-rLe.LsfxCIu', '.', 'm/sub/'],
        greeting_version=30,
    )
    client.pull(str(dest), preserve_times=False, preserve_perms=False)
except Exception as exc:  # noqa: BLE001 - retain daemon stderr for the oracle
    failure = repr(exc)
daemon_stderr = finish_stdio_daemon(client, daemon)

got = dest / 'parent-link'
if failure or not got.is_file() or got.is_symlink() or got.read_text() != PAYLOAD:
    test_fail(
        'daemon -rL failed to dereference an in-module parent-relative '
        f'symlink (client_error={failure}, exists={got.exists()}, '
        f'stderr={daemon_stderr.strip()!r})'
    )

print('daemon-copylinks-parent-target: safe ../ target was dereferenced')

#!/usr/bin/env python3

import os

from harness.rsync import (
    SCRATCHDIR, forced_protocol, makepath, rmtree, test_fail, test_skipped,
    write_daemon_conf,
)
from harness.daemon import finish_stdio_daemon, start_stdio_daemon

_proto = forced_protocol()
if _proto is not None and _proto < 30:
    test_skipped(f'the stdio daemon client speaks protocol 30 (forced {_proto})',
                 capability='protocol_30')

PAYLOAD = 'safe-parent-relative-target\n'

base = SCRATCHDIR / 'daemon-copy-links-parent'
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
except Exception as exc:
    failure = repr(exc)
daemon_stderr = finish_stdio_daemon(client, daemon)

got = dest / 'parent-link'
if failure or not got.is_file() or got.is_symlink() or got.read_text() != PAYLOAD:
    test_fail(
        'daemon -rL failed to dereference an in-module parent-relative '
        f'symlink (client_error={failure}, exists={got.exists()}, '
        f'stderr={daemon_stderr.strip()!r})'
    )

print('daemon-copy-links-parent-target: safe ../ target was dereferenced')

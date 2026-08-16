#!/usr/bin/env python3
"""A write client must not use a forged dot entry to wipe a daemon module."""

import time

from rsyncfns import (
    SCRATCHDIR, claim_ports, makepath, require_tcp, rmtree, start_rsyncd,
    test_fail,
)
import rsync_proto as rp

PORT = 13013

require_tcp("the malicious daemon client needs a real TCP socket; run with --use-tcp")
claim_ports(PORT)

base = SCRATCHDIR / 'daemon-dot-file-force-wipe'
rmtree(base)
mod = base / 'mod'
makepath(mod / 'private')
sentinel = mod / 'private' / 'must-survive'
sentinel.write_text('pre-existing module data\n')

conf = base / 'rsyncd.conf'
conf.write_text(f"""\
pid file = {base}/rsyncd.pid
use chroot = no

[mod]
    path = {mod}
    read only = no
    write only = yes
    refuse options = delete
""")
start_rsyncd(conf, PORT)

s = rp.DaemonClient('127.0.0.1', PORT)
s.handshake(
    'mod',
    ['--server', '-re.LsfxCIu', '--force', '--no-inc-recursive', '.', 'mod/'],
    greeting_version=30,
)
s.send_flat_flist([
    rp.FileEntry('.', mode=rp.S_IFREG | 0o644, length=0),
])
s.drain(timeout=5)
s.close()

for _ in range(50):
    if not sentinel.exists():
        break
    time.sleep(0.02)

if not sentinel.exists():
    test_fail(
        'write-only client bypassed refuse-options=delete by sending a regular '
        'dot entry with --force and recursively wiped the daemon module root')

print('daemon-dot-file-force-wipe: daemon rejected a non-directory dot root '
      'from a write-only client')

#!/usr/bin/env python3

import glob
import os

from harness.rsync import (
    SCRATCHDIR, claim_ports, require_asan, require_tcp, start_rsyncd, test_fail,
)
from harness import protocol as rp

PORT = 12953
require_tcp("the pure-Python sender needs a real TCP daemon; run with --use-tcp")
require_asan("the FLAG_HLINKED pool underflow is only observable under "
            "AddressSanitizer + the lib/pool_alloc.c redzone")
claim_ports(PORT)

mod = SCRATCHDIR / 'hlink-oob-mod'
mod.mkdir(parents=True, exist_ok=True)
conf = SCRATCHDIR / 'hlink-oob.conf'
conf.write_text(f"""\
pid file = {SCRATCHDIR}/hlink-oob-rsyncd.pid
use chroot = no

[mod]
    path = {mod}
    read only = no
""")

asan_log = SCRATCHDIR / 'hlink-oob-asan'
for stale in glob.glob(f"{asan_log}.*"):
    os.unlink(stale)
prev = os.environ.get('ASAN_OPTIONS', '')
os.environ['ASAN_OPTIONS'] = (
    (prev + ':' if prev else '') + f'detect_leaks=0:abort_on_error=1:log_path={asan_log}')

start_rsyncd(conf, PORT)

s = rp.DaemonSender('127.0.0.1', PORT)
s.handshake('mod', ['--server', '-re.iLsfxCIu', '--checksum', '.', 'mod/'],
            greeting_version=30)
evil = rp.FileEntry('h.txt', mode=rp.S_IFREG | 0o644, length=0,
                    extra_flags=rp.XMIT_HLINKED | rp.XMIT_HLINK_FIRST,
                    csum=b'\x41' * 64)
s.send_flat_flist([evil])
s.drain(timeout=3.0)
s.close()

reports = glob.glob(f"{asan_log}.*")
text = ''.join(open(r, errors='replace').read() for r in reports)
if 'AddressSanitizer' in text:
    test_fail(
        "receiver hit an AddressSanitizer error parsing an XMIT_HLINKED entry "
        "sent without -H -- the FLAG_HLINKED pool underflow is unguarded:\n"
        + text[:1500])

print("hardlink-flag-oob: XMIT_HLINKED-without-H entry parsed with no "
      "pool underflow (FLAG_HLINKED gated on preserve_hard_links).")

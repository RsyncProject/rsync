#!/usr/bin/env python3

import glob as globmod
import os
import time

from harness.rsync import (
    SCRATCHDIR, claim_ports, require_asan, require_tcp, start_rsyncd, test_fail,
)
from harness import protocol as rp

PORT = 12956
require_tcp("the pure-Python client needs a real TCP daemon; run with --use-tcp")
require_asan("the read_args trailing-NULL argv overflow is only observable under "
            "AddressSanitizer")
claim_ports(PORT)

mod = SCRATCHDIR / 'readargs-mod'
mod.mkdir(parents=True, exist_ok=True)
for stale in mod.iterdir():
    stale.unlink()
NFILES = 995
for i in range(NFILES):
    (mod / ('f%04d' % i)).write_text('x')

conf = SCRATCHDIR / 'readargs.conf'
conf.write_text(f"""\
pid file = {SCRATCHDIR}/readargs-rsyncd.pid
use chroot = no

[mod]
    path = {mod}
    read only = no
""")

asan_log = SCRATCHDIR / 'readargs-asan'
for stale in globmod.glob(f"{asan_log}.*"):
    os.unlink(stale)
prev = os.environ.get('ASAN_OPTIONS', '')
os.environ['ASAN_OPTIONS'] = (
    (prev + ':' if prev else '') + f'detect_leaks=0:abort_on_error=1:log_path={asan_log}')

start_rsyncd(conf, PORT)

c = rp.DaemonClient('127.0.0.1', PORT)
c.handshake('mod', ['--server', '--sender', '-e.LsfxCIu', '.', '*'],
            greeting_version=30)
c.drain(timeout=3.0)
c.close()

reports = ''
for _ in range(30):
    reports = ''.join(open(r, errors='replace').read()
                      for r in globmod.glob(f"{asan_log}.*"))
    if 'AddressSanitizer' in reports:
        break
    time.sleep(0.1)

if 'AddressSanitizer' in reports:
    test_fail(
        "read_args() wrote the trailing argv NULL one slot past the argv heap "
        "allocation when a post-dot glob landed argc exactly on maxargs:\n"
        + reports[:1500])

print("io-readargs-argv-nullwrite: the trailing argv NULL stays in bounds when a "
      "post-dot glob fills argv to maxargs.")

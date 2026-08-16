#!/usr/bin/env python3
# Regression test for an off-by-one heap write in read_args() (io.c). After the
# '.' separator, read_args delegates argv growth to glob_expand(), which does
# ENSURE_MEMSPACE(glob.argv, char *, glob.maxargs, glob.argc + 1) -- reserving
# room for the entry it adds but not for the trailing NULL that read_args writes
# after the loop. ENSURE_MEMSPACE only grows when argc+1 > maxargs, so when a
# single post-dot glob lands argc exactly on maxargs (initially MAX_ARGS = 1000),
# no growth happens and the final 'argv[argc] = NULL' is an 8-byte NULL write one
# slot past the argv allocation. The pre-dot growth check (argc == maxargs-1)
# never fires once dot_pos is set, and it cannot pre-empt a single glob that
# crosses the boundary within one iteration.
#
# A daemon client sends '--server --sender -e... . *' against a module holding
# exactly 995 files: read_args adds "rsyncd" + 3 options + "." (argc 5), the lone
# post-dot '*' globs to 995 entries -> argc lands on exactly 1000 == maxargs, and
# argv[1000] is written one past the 1000-slot array. Reachable post-chroot/setuid
# in the per-connection daemon child. The fix reserves the +1 slot before the
# NULL store.
#
# Oracle: pre-fix -> the daemon child hits an ASan heap-buffer-overflow report;
# fixed -> none. Needs an ASan build + a real TCP daemon, and is skipped
# otherwise.

import glob as globmod
import os
import time

from rsyncfns import (
    SCRATCHDIR, claim_ports, require_asan, require_tcp, start_rsyncd, test_fail,
)
import rsync_proto as rp

PORT = 12956
require_tcp("the pure-Python client needs a real TCP daemon; run with --use-tcp")
require_asan("the read_args trailing-NULL argv overflow is only observable under "
            "AddressSanitizer")
claim_ports(PORT)

mod = SCRATCHDIR / 'readargs-mod'
mod.mkdir(parents=True, exist_ok=True)
for stale in mod.iterdir():
    stale.unlink()
# read_args pre-dot args are: "rsyncd" + --server + --sender + -e.LsfxCIu + "."
# = 5; the lone post-dot '*' must glob to 995 so argc lands on MAX_ARGS (1000).
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
# A single post-dot '*' that globs to NFILES entries lands argc on maxargs.
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

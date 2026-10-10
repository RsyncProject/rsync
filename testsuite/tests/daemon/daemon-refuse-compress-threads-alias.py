#!/usr/bin/env python3

import json
import os
import re
import sys
import time

from harness.rsync import (
    SCRATCHDIR, claim_ports, require_tcp, rmtree, run_rsync,
    start_rsyncd, test_fail, test_skipped,
)
from harness import protocol as rp
from harness import process_thread_count

PORT = 13121
REQUESTED_THREADS = 256
MAX_SAFE_THREADS = 64

require_tcp("the malicious receiver needs a real TCP daemon; run with --use-tcp")
claim_ports(PORT)

version = json.loads(run_rsync('-VV', check=True, capture_output=True).stdout)
if 'zstd' not in version.get('compress_list', []):
    test_skipped('this build does not include Zstandard', capability='zstd_threads')

base = SCRATCHDIR / 'daemon-refuse-compress-threads-alias'
rmtree(base)
mod = base / 'module'
mod.mkdir(parents=True)
(mod / 'f').write_bytes(os.urandom(2 * 1024 * 1024))

log = base / 'rsyncd.log'
conf = base / 'rsyncd.conf'
conf.write_text(f"""\
pid file = {base}/rsyncd.pid
log file = {log}
use chroot = no

[mod]
    path = {mod}
    refuse options = compress-threads
""")
start_rsyncd(conf, PORT)

def refusal_logged(spelling):
    needle = f'configured to refuse --{spelling}'
    for _ in range(100):
        if log.exists() and needle in log.read_text(errors='replace'):
            return True
        time.sleep(0.02)
    return False

control = rp.DaemonClient('127.0.0.1', PORT)
control_rejected = False
try:
    control.handshake(
        'mod',
        [
            '--server', '--sender', '-e.LsfxCIu', '--compress',
            '--compress-choice=zstd', '--compress-threads=2', '.', 'mod/f',
        ],
        greeting_version=30,
    )
    control.recv_flist(preserve_links=False)
except (rp.ProtocolError, OSError):
    control_rejected = True
finally:
    control.close()
if not control_rejected or not refusal_logged('compress-threads'):
    test_fail('canonical --compress-threads unexpectedly bypassed its refuse rule')

c = rp.DaemonClient('127.0.0.1', PORT)
try:
    c.handshake(
        'mod',
        [
            '--server', '--sender', '-e.LsfxCIu', '--compress',
            '--compress-choice=zstd', f'--zt={REQUESTED_THREADS}', '.', 'mod/f',
        ],
        greeting_version=30,
    )
    entries = rp.sort_entries(c.recv_flist(preserve_links=False))
except (rp.ProtocolError, OSError):
    c.close()
    if not refusal_logged('zt'):
        test_fail('the alias connection ended, but the daemon never logged a '
                  '--zt refusal, so it was not the refuse rule that stopped it')
    print('daemon refuse rule rejected both --compress-threads and --zt')
    sys.exit(0)

ndx = next(i for i, entry in enumerate(entries) if entry.name == b'f')
c.send_data(c.make_request(ndx) + c.w_ndx(rp.NDX_DONE))

child_pid = None
for _ in range(100):
    matches = re.findall(r'\[(\d+)\] rsync on mod/f',
                         log.read_text(errors='replace') if log.exists() else '')
    if matches:
        child_pid = int(matches[-1])
        break
    time.sleep(0.02)
if child_pid is None:
    c.close()
    test_fail('could not identify the per-connection daemon sender')

observed = 0
for _ in range(100):
    observed = max(observed, process_thread_count(child_pid))
    if observed >= REQUESTED_THREADS:
        break
    time.sleep(0.02)
c.close()

how_many = (f'and it created {observed} worker threads'
            if observed > 0 else
            'though its thread count could not be read here')
test_fail(f'canonical --compress-threads was refused but its --zt alias was '
          f'accepted: the daemon served the file list {how_many}.  A refuse '
          f'rule naming one spelling of an option must cover every spelling '
          f'of it')

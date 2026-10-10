#!/usr/bin/env python3

import json
import os
import re
import sys
import time

from harness.rsync import (
    SCRATCHDIR, claim_ports, require_tcp, rmtree, run_rsync, start_rsyncd,
    test_fail, test_skipped,
)
from harness import protocol as rp
from harness import process_thread_count

PORT = 13120
REQUESTED_THREADS = 256
MAX_DAEMON_COMPRESSION_THREADS = 8
MAX_SAFE_THREADS = MAX_DAEMON_COMPRESSION_THREADS + 1

require_tcp("the malicious receiver needs a real TCP daemon; run with --use-tcp")
claim_ports(PORT)

version = json.loads(run_rsync('-VV', check=True, capture_output=True).stdout)
if 'zstd' not in version.get('compress_list', []):
    test_skipped('this build does not include Zstandard', capability='zstd_threads')

base = SCRATCHDIR / 'daemon-zstd-thread-exhaustion'
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
""")
start_rsyncd(conf, PORT)

c = rp.DaemonClient('127.0.0.1', PORT)
c.handshake(
    'mod',
    [
        '--server', '--sender', '-e.LsfxCIu', '--compress',
        '--compress-choice=zstd',
        f'--compress-threads={REQUESTED_THREADS}', '.', 'mod/f',
    ],
    greeting_version=30,
)
entries = rp.sort_entries(c.recv_flist(preserve_links=False))
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

if process_thread_count(child_pid) < 0:
    c.close()
    test_skipped("counting a process's threads is unsupported on "
                 f'{sys.platform}, so a bound on the worker pool cannot be '
                 'observed here', capability='thread_count')

observed = 0
for _ in range(100):
    observed = max(observed, process_thread_count(child_pid))
    if observed >= REQUESTED_THREADS:
        break
    time.sleep(0.02)
c.close()

if observed == 0:
    test_fail('the daemon sender exited before its thread count could be read')
if observed < 2:
    test_skipped('this build creates no Zstandard worker threads, so a bound '
                 f'on them cannot be demonstrated (observed {observed})',
                 capability='zstd_threads')
if observed > MAX_SAFE_THREADS:
    test_fail(
        f'one unauthenticated daemon client requested {REQUESTED_THREADS} '
        f'Zstandard workers and the per-connection sender materialized '
        f'{observed} threads'
    )

print(f'daemon-zstd-thread-exhaustion: sender stayed at {observed} threads')

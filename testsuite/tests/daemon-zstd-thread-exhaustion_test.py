#!/usr/bin/env python3
"""A daemon client must not control an unbounded Zstandard worker count.

The daemon parses the peer-supplied server argv, so a client that names a
large --compress-threads (or its --zt alias) on a PULL makes the daemon-side
sender materialize that many Zstandard workers.  A stock client can do this
with -M--compress-threads=N; no custom client is needed.  On an anonymous
module no authentication is required first.

The bound asserted here is the implementation's own: MAX_DAEMON_COMPRESSION
_THREADS workers plus the main thread.  A looser threshold would go green on a
build that simply cannot create the workers -- a libzstd without
multithreading, or a process thread limit -- so the test first proves that a
threaded worker pool IS reachable here, and only then that it is bounded.
"""

import json
import os
import re
import subprocess
import sys
import time

from rsyncfns import (
    SCRATCHDIR, claim_ports, require_tcp, rmtree, run_rsync, start_rsyncd,
    test_fail, test_skipped,
)
import rsync_proto as rp

PORT = 13120
REQUESTED_THREADS = 256
# options.c: MAX_DAEMON_COMPRESSION_THREADS, plus the main thread.
MAX_DAEMON_COMPRESSION_THREADS = 8
MAX_SAFE_THREADS = MAX_DAEMON_COMPRESSION_THREADS + 1

require_tcp("the malicious receiver needs a real TCP daemon; run with --use-tcp")
claim_ports(PORT)

version = json.loads(run_rsync('-VV', check=True, capture_output=True).stdout)
if 'zstd' not in version.get('compress_list', []):
    test_skipped('this build does not include Zstandard')

base = SCRATCHDIR / 'daemon-zstd-thread-exhaustion'
rmtree(base)
mod = base / 'module'
mod.mkdir(parents=True)
# Incompressible output fills the socket while the client deliberately stops
# reading, keeping the per-connection process alive for a thread snapshot.
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

# Identify the exact per-connection sender from the daemon log.
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


def thread_count(pid):
    if sys.platform == 'darwin':
        proc = subprocess.run(['ps', '-M', str(pid)], capture_output=True,
                              text=True)
        return max(0, len(proc.stdout.splitlines()) - 1)
    if sys.platform.startswith('linux'):
        try:
            status = open(f'/proc/{pid}/status').read()
        except OSError:
            return 0
        match = re.search(r'^Threads:\s+(\d+)', status, re.M)
        return int(match.group(1)) if match else 0
    return -1


# Ask once whether this platform can be counted at all, BEFORE folding the
# answer into a max(): the -1 meaning "unsupported" never survives
# max(0, -1), so testing for it afterwards can never fire, and an uncountable
# platform instead looks like a sender that died before it could be read.
# Cygwin reported exactly that.
if thread_count(child_pid) < 0:
    c.close()
    test_skipped("counting a process's threads is unsupported on "
                 f'{sys.platform}, so a bound on the worker pool cannot be '
                 'observed here')

observed = 0
for _ in range(100):
    observed = max(observed, thread_count(child_pid))
    if observed >= REQUESTED_THREADS:
        break
    time.sleep(0.02)
c.close()

if observed == 0:
    test_fail('the daemon sender exited before its thread count could be read')
if observed < 2:
    # Only the main thread: this libzstd cannot make workers at all (built
    # without multithreading, or a thread limit).  The bound below would then
    # hold for a reason that has nothing to do with the cap, so do not claim
    # to have tested it.
    test_skipped('this build creates no Zstandard worker threads, so a bound '
                 f'on them cannot be demonstrated (observed {observed})')
if observed > MAX_SAFE_THREADS:
    test_fail(
        f'one unauthenticated daemon client requested {REQUESTED_THREADS} '
        f'Zstandard workers and the per-connection sender materialized '
        f'{observed} threads'
    )

print(f'daemon-zstd-thread-exhaustion: sender stayed at {observed} threads')

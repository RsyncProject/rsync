#!/usr/bin/env python3
"""``refuse options = compress-threads`` must also refuse its ``--zt`` alias.

set_refuse_options() stopped after the first exact long-option match, so the
canonical compress-threads entry was disabled but its later zt alias stayed
accepted.  Both halves here build the daemon argv by hand -- a stock client
does not forward this option at all unless asked to with -M, and the raw
protocol is what preserves the exact spelling under test.

The oracle is the refusal itself: the alias connection must be torn down and
the daemon must log "configured to refuse --zt".  It is deliberately NOT the
worker count.  An accepted --zt that happens to produce few threads is still a
defeated refuse rule, and several unrelated things bound that count -- a
separate daemon worker cap (see the Zstandard thread-cap change), a libzstd
built without multithreading, or a process/thread limit -- so a count-based
assertion goes green while the rule is bypassed.  The count is still measured,
but only to say how much capability the bypass delivered.
"""

import json
import os
import re
import subprocess
import sys
import time

from rsyncfns import (
    SCRATCHDIR, claim_ports, require_tcp, rmtree, run_rsync,
    start_rsyncd, test_fail, test_skipped,
)
import rsync_proto as rp

PORT = 13121
REQUESTED_THREADS = 256
MAX_SAFE_THREADS = 64

require_tcp("the malicious receiver needs a real TCP daemon; run with --use-tcp")
claim_ports(PORT)

version = json.loads(run_rsync('-VV', check=True, capture_output=True).stdout)
if 'zstd' not in version.get('compress_list', []):
    test_skipped('this build does not include Zstandard')

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


# Control: preserve the canonical spelling in a hand-built daemon argv and
# verify that the configured rule rejects it before any file list is served.
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

# Attack: preserve the accepted alias spelling in a hand-built daemon argv.
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

# Reaching here means the daemon served a file list for --zt: the refuse rule
# did not fire and the capability was delivered.  That is the bug, whatever
# the worker count turns out to be.  Measure it anyway, to report what the
# bypass was worth.
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


observed = 0
for _ in range(100):
    observed = max(observed, thread_count(child_pid))
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

#!/usr/bin/env python3

import os
import re
import signal
import subprocess
import sys
import time

from harness.rsync import (
    FROMDIR, SCRATCHDIR, TODIR, claim_ports, make_data_file, makepath, rmtree,
    rsync_argv, start_rsyncd, test_fail, test_skipped, build_rsyncd_conf,
    USE_TCP,
)

KNOWN_RERR = {
    0,
    1,
    2,
    3,
    4,
    5,
    10,
    11,
    12,
    13,
    14,
    15,
    16,
    19,
    20,
    21,
    22,
    23,
    24,
    25,
    30,
    35,
    124,
    125,
    126,
    127,
}

PORT = 12966

if not USE_TCP:
    test_skipped("needs --use-tcp to kill the daemon (sender) mid-transfer", capability='tcp')

conf = build_rsyncd_conf()

rmtree(FROMDIR)
rmtree(TODIR)
makepath(FROMDIR)
makepath(TODIR)
make_data_file(FROMDIR / 'bigfile', 8 * 1024 * 1024)

claim_ports(PORT)
daemon = start_rsyncd(conf, PORT)

client = subprocess.Popen(
    rsync_argv('-a', '--bwlimit=64', '--timeout=60',
               f'rsync://127.0.0.1:{PORT}/test-from/', f'{TODIR}/'),
    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
)

child_pids = []
deadline = time.monotonic() + 10
while time.monotonic() < deadline:
    try:
        logtext = (SCRATCHDIR / 'rsyncd.log').read_text()
        child_pids = [int(pid) for pid in re.findall(r'\[(\d+)\] rsync on ', logtext)]
    except OSError:
        pass
    if child_pids:
        break
    if client.poll() is not None:
        break
    time.sleep(0.05)
if not child_pids:
    client.kill()
    out, _ = client.communicate()
    test_fail(f"daemon sender did not start before the transfer exited:\n{out}")
for pid in child_pids:
    try:
        os.kill(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
if daemon.poll() is None:
    daemon.kill()
    try:
        daemon.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass

try:
    out, _ = client.communicate(timeout=30)
except subprocess.TimeoutExpired:
    client.kill()
    client.communicate()
    test_fail("receiver did not exit after the sender was killed (hung)")

rc = client.returncode
if rc not in KNOWN_RERR:
    sys.stderr.write(
        f"receiver exited with {rc}, which is NOT a documented RERR_* value; "
        f"output:\n{out}\n"
    )
    sys.exit(1)

if rc == 0:
    print("io-error-mask: receiver exited 0 (transfer may have completed "
          "before the kill); no out-of-range exit code observed.")
else:
    print(f"io-error-mask: receiver exited {rc} (a documented RERR_* "
          f"value) after the sender was killed mid-transfer; no arbitrary "
          f"io_error bit propagated to the exit code.")

sys.exit(0)

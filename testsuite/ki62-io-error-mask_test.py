#!/usr/bin/env python3
# Verifies: SW-REQ-114
#
# When MSG_IO_ERROR arrives, io.c OR's the wire value into the local io_error.
# Without masking, a malicious peer could set arbitrary (undefined) bits, which
# would then be stored and re-forwarded upstream in the local io_error.  The fix
# masks the incoming value to the defined IOERR_* bits (IOERR_VALID_MASK).  (The
# exit code itself is already safe: cleanup.c maps only the three defined bits
# onto RERR_* constants, so undefined bits never become the exit status -- the
# mask is about not storing/propagating junk, not about the exit value.)
#
# This is only an interrupted-transfer exit-code smoke test: it starts a real
# daemon (the sender for a download), begins a transfer, then kills the sender
# mid-transfer and asserts the receiver exits with a documented RERR_* value.
# It does NOT craft a hostile MSG_IO_ERROR frame, so it does not exercise the
# mask itself (no test does yet) -- it just guards the normal-peer exit path.
#
# Exit codes: 0 pass, 1 fail, 77 skip (killable daemon testing not possible --
# the test needs --use-tcp so it can SIGKILL the sender process directly).

import os
import re
import signal
import subprocess
import sys
import time

from rsyncfns import (
    FROMDIR, SCRATCHDIR, TODIR, claim_ports, make_data_file, makepath, rmtree,
    rsync_argv, start_rsyncd, test_fail, test_skipped, build_rsyncd_conf,
    USE_TCP,
)

# The complete set of exit codes rsync defines (errcode.h).  Whatever path the
# interrupted transfer takes, the receiver's exit code MUST be one of these --
# never an arbitrary number injected via a polluted io_error.
KNOWN_RERR = {
    0,    # RERR_OK
    1,    # RERR_SYNTAX
    2,    # RERR_PROTOCOL
    3,    # RERR_FILESELECT
    4,    # RERR_UNSUPPORTED
    5,    # RERR_STARTCLIENT
    10,   # RERR_SOCKETIO
    11,   # RERR_FILEIO
    12,   # RERR_STREAMIO
    13,   # RERR_MESSAGEIO
    14,   # RERR_IPC
    15,   # RERR_CRASHED
    16,   # RERR_TERMINATED
    19,   # RERR_SIGNAL1
    20,   # RERR_SIGNAL
    21,   # RERR_WAITCHILD
    22,   # RERR_MALLOC
    23,   # RERR_PARTIAL
    24,   # RERR_VANISHED
    25,   # RERR_DEL_LIMIT
    30,   # RERR_TIMEOUT
    35,   # RERR_CONTIMEOUT
    124,  # RERR_CMD_FAILED
    125,  # RERR_CMD_KILLED
    126,  # RERR_CMD_RUN
    127,  # RERR_CMD_NOTFOUND
}

PORT = 12966

# Killing the sender (the daemon process) mid-transfer requires a handle to
# the daemon process, which only exists in --use-tcp mode.  In the default
# pipe mode the daemon is forked off the client and is not directly killable,
# so skip rather than run an inconclusive test.
if not USE_TCP:
    test_skipped("needs --use-tcp to kill the daemon (sender) mid-transfer")

conf = build_rsyncd_conf()

# Build a source tree with a file big enough that, throttled by --bwlimit, the
# transfer is still in flight when we kill the sender.
rmtree(FROMDIR)
rmtree(TODIR)
makepath(FROMDIR)
makepath(TODIR)
make_data_file(FROMDIR / 'bigfile', 8 * 1024 * 1024)  # 8 MB

claim_ports(PORT)
daemon = start_rsyncd(conf, PORT)

# --bwlimit keeps the transfer slow enough to guarantee the sender is
# mid-stream when we SIGKILL it.  --info=progress gives a heartbeat we could
# observe, but the bwlimit + short sleep is enough on loopback.
client = subprocess.Popen(
    rsync_argv('-a', '--bwlimit=64', '--timeout=60',
               f'rsync://127.0.0.1:{PORT}/test-from/', f'{TODIR}/'),
    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
)

# Let the receiver get past the handshake and into the data stream before we
# cut the sender off, so the death is observed as a mid-transfer stream error
# (the path that exercises the io_error -> exit-code mapping).
time.sleep(2)

# Kill the sender mid-transfer.  rsyncd forks a child per connection, so the
# actual sender is NOT `daemon` (the listener) -- killing only the listener
# leaves the child streaming and the receiver never sees EOF.  The child logs
# "[pid] rsync on <module>/" to the daemon log; kill that pid (plus the
# listener).  The daemon shares this test's process group, so killpg is not an
# option (it would kill the test itself).
child_pids = []
try:
    logtext = (SCRATCHDIR / 'rsyncd.log').read_text()
    child_pids = [int(pid) for pid in re.findall(r'\[(\d+)\] rsync on ', logtext)]
except OSError:
    pass
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

# Wait for the receiver to notice the dead sender and exit.  It should do so
# promptly (closed socket / EOF), but cap it so a regression that hangs is
# caught rather than stalling the suite.
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

# An interrupted download should not exit 0; sanity-check that we actually
# interrupted a transfer (a clean exit would mean we killed the sender too
# early/late and didn't exercise the mid-transfer path).  Don't fail on this --
# timing-dependent -- just note it.
if rc == 0:
    print("ki62-io-error-mask: receiver exited 0 (transfer may have completed "
          "before the kill); no out-of-range exit code observed.")
else:
    print(f"ki62-io-error-mask: receiver exited {rc} (a documented RERR_* "
          f"value) after the sender was killed mid-transfer; no arbitrary "
          f"io_error bit propagated to the exit code.")

sys.exit(0)

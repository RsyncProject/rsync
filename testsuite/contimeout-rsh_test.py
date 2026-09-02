#!/usr/bin/env python3
# --contimeout is documented as a "daemon connection timeout".  The guard that
# rejects it only looked at whether connect_timeout was set, so a daemon
# connection made through a remote shell (e.g. rsync-ssl, which runs rsync with
# --rsh pointing at its helper) was rejected with the same syntax error as a
# plain non-daemon remote-shell transfer.  Only reject the option when there is
# no daemon connection at all: a daemon reached via --rsh (daemon_connection ==
# 1) is still a daemon connection, and rsync now also times that connection's
# establishment phase with --contimeout the same way it times a socket connect.

import subprocess
import time

from rsyncfns import SCRATCHDIR, SRCDIR, rsync_argv, rmtree, test_fail

RERR_CONTIMEOUT = 35

base = SCRATCHDIR / 'contimeout-rsh'
rmtree(base)
base.mkdir(parents=True)


def run(*args):
    return subprocess.run(rsync_argv(*args), capture_output=True, text=True)


rejected_marker = "may only be used when connecting to an rsync daemon"

# A remote-shell command that fails immediately: the option guard runs before
# rsync ever tries to exec it, so it only has to exist as a plausible --rsh
# target to put rsync into its daemon-via-rsh connection mode.
rsh_prog = str(SRCDIR / 'support' / 'lsh.sh')

# --- Daemon via --rsh must accept --contimeout (rsync-ssl's shape of call).
proc = run('--contimeout=5', '--rsh=' + rsh_prog,
           '-av', 'rsync://127.0.0.1:9/mod/', str(base / 'dest'))
if rejected_marker in (proc.stderr or ''):
    test_fail(f"--contimeout was rejected for a daemon-via-rsh connection:\n{proc.stderr}")

# --- A plain remote-shell (non-daemon) destination must still be rejected.
proc = run('--contimeout=5', '-av', str(base / 'src'), 'localhost:' + str(base / 'dst'))
if rejected_marker not in (proc.stderr or ''):
    test_fail("--contimeout was not rejected for a non-daemon remote shell:\n" +
              (proc.stderr or '') + (proc.stdout or ''))

# --- A daemon-via-rsh connection that never establishes must time out with the
# daemon-connection timeout exit code.  The fake helper sleeps instead of
# connecting, so the only thing that can end the run is --contimeout firing.
fake_rsh = base / 'hang-rsh'
# The helper inherits rsync's stderr; redirect it so an orphaned "sleep" does
# not keep the harness's captured-pipe open after rsync has already exited.
fake_rsh.write_text("#!/bin/sh\nexec 2>/dev/null\nsleep 60\n")
fake_rsh.chmod(0o755)

start = time.monotonic()
proc = run('--contimeout=1', '--rsh=' + str(fake_rsh),
           '-av', 'rsync://127.0.0.1:9/mod/', str(base / 'dest2'))
elapsed = time.monotonic() - start

if proc.returncode != RERR_CONTIMEOUT:
    test_fail(f"--contimeout did not abort the hung connection with exit "
              f"{RERR_CONTIMEOUT}; got {proc.returncode}:\n{proc.stderr}")
if elapsed >= 15:
    test_fail(f"--contimeout=1 took {elapsed:.1f}s; the timeout did not bound "
              "the connection establishment phase")

print("contimeout-rsh: --contimeout is accepted for a daemon-via-rsh "
      "connection, rejected for a non-daemon remote shell, and times out a "
      "connection that never establishes")

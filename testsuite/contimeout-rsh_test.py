#!/usr/bin/env python3
# --contimeout is documented as a "daemon connection timeout".  The guard that
# rejects it only looked at whether connect_timeout was set, so a daemon
# connection made through a remote shell (e.g. rsync-ssl, which runs rsync with
# --rsh pointing at its helper) was rejected with the same syntax error as a
# plain non-daemon remote-shell transfer.  Only reject the option when there is
# no daemon connection at all: a daemon reached via --rsh (daemon_connection ==
# 1) is still a daemon connection.

import subprocess

from rsyncfns import SRCDIR, rsync_argv, test_fail


def run(*args):
    return subprocess.run(rsync_argv(*args), capture_output=True, text=True)


rejected_marker = "may only be used when connecting to an rsync daemon"

# A remote-shell command that fails immediately: the option guard runs before
# rsync ever tries to exec it, so it only has to exist as a plausible --rsh
# target to put rsync into its daemon-via-rsh connection mode.
rsh_prog = str(SRCDIR / 'support' / 'lsh.sh')

# --- Daemon via --rsh must accept --contimeout (rsync-ssl's shape of call).
proc = run('--contimeout=5', '--rsh=' + rsh_prog,
           '-av', 'rsync://127.0.0.1:9/mod/', '/tmp/contimeout-rsh-dest')
if rejected_marker in (proc.stderr or ''):
    test_fail(f"--contimeout was rejected for a daemon-via-rsh connection:\n{proc.stderr}")

# --- A plain remote-shell (non-daemon) destination must still be rejected.
proc = run('--contimeout=5', '-av', '/tmp/contimeout-rsh-src', 'localhost:/tmp/contimeout-rsh-dst')
if rejected_marker not in (proc.stderr or ''):
    test_fail("--contimeout was not rejected for a non-daemon remote shell:\n" +
              (proc.stderr or '') + (proc.stdout or ''))

print("contimeout-rsh: --contimeout is accepted for a daemon-via-rsh "
      "connection and rejected for a non-daemon remote shell")

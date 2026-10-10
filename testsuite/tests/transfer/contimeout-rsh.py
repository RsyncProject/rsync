#!/usr/bin/env python3

import subprocess
import time

from harness.rsync import SCRATCHDIR, SRCDIR, rsync_argv, rmtree, test_fail

RERR_CONTIMEOUT = 35

base = SCRATCHDIR / 'contimeout-rsh'
rmtree(base)
base.mkdir(parents=True)

def run(*args):
    return subprocess.run(rsync_argv(*args), capture_output=True, text=True)

rejected_marker = "may only be used when connecting to an rsync daemon"

rsh_prog = str(SRCDIR / 'support' / 'lsh.sh')

proc = run('--contimeout=5', '--rsh=' + rsh_prog,
           '-av', 'rsync://127.0.0.1:9/mod/', str(base / 'dest'))
if rejected_marker in (proc.stderr or ''):
    test_fail(f"--contimeout was rejected for a daemon-via-rsh connection:\n{proc.stderr}")

proc = run('--contimeout=5', '-av', str(base / 'src'), 'localhost:' + str(base / 'dst'))
if rejected_marker not in (proc.stderr or ''):
    test_fail("--contimeout was not rejected for a non-daemon remote shell:\n" +
              (proc.stderr or '') + (proc.stdout or ''))

fake_rsh = base / 'hang-rsh'
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

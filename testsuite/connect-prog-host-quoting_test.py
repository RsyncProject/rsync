#!/usr/bin/env python3
import os
import subprocess

from rsyncfns import SCRATCHDIR, rmtree, rsync_argv, test_fail, test_skipped

base = SCRATCHDIR / 'connect-prog-quote'
rmtree(base)
base.mkdir(parents=True)
sentinel = base / 'pwned'
host = f"localhost;touch${{IFS}}{sentinel};#"
env = os.environ.copy()
env['RSYNC_CONNECT_PROG'] = 'printf %H >/dev/null'

proc = subprocess.run(
    rsync_argv(f'rsync://{host}/mod/', str(base / 'out')),
    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
out = (proc.stdout or '') + (proc.stderr or '')

if 'socketpair_tcp failed' in out:
    test_skipped("RSYNC_CONNECT_PROG socketpair is blocked in this environment")
if sentinel.exists():
    test_fail("RSYNC_CONNECT_PROG %H substitution executed shell syntax from the host")
if proc.returncode == 0:
    test_fail("malicious connect-program probe unexpectedly completed a transfer")

print("connect-prog-host-quoting: %H host shell syntax is quoted")

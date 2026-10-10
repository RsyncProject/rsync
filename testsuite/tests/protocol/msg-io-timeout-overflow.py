#!/usr/bin/env python3

import os
import signal
import subprocess

from harness.rsync import (
    SCRATCHDIR, build_patched_rsync, makepath, rmtree, test_fail,
)

INT_MAX = 2147483647
DEADLINE = 25

victim = build_patched_rsync('io-timeout-fwrapv', [], append_cflags='-fwrapv')

base = SCRATCHDIR / 'io-timeout'
rmtree(base)
src = base / 'src'
dst = base / 'dst'
makepath(src)
makepath(dst)
(src / 'file').write_text("payload\n")

proc = subprocess.Popen(
    [str(victim), '--timeout=%d' % INT_MAX, '-a', str(src) + '/', str(dst) + '/'],
    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    start_new_session=True)
try:
    out, _ = proc.communicate(timeout=DEADLINE)
except subprocess.TimeoutExpired:
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except ProcessLookupError:
        pass
    proc.communicate()
    test_fail(
        f'--timeout={INT_MAX} did not finish within {DEADLINE}s')

if proc.returncode != 0:
    test_fail(f"rsync did not spin under --timeout={INT_MAX} but exited "
              f"non-zero (rc={proc.returncode}).  Output tail:\n"
              + '\n'.join(out.splitlines()[-20:]))
if not (dst / 'file').is_file():
    test_fail(f"rsync returned 0 under --timeout={INT_MAX} but did not copy the "
              f"file.  Output tail:\n" + '\n'.join(out.splitlines()[-20:]))

print("msg-io-timeout-overflow: -fwrapv rsync absorbed --timeout=INT_MAX without "
      "spinning; the copy completed normally.")

#!/usr/bin/env python3

import os
import shlex
import signal
import subprocess
import sys

signal.signal(signal.SIGUSR1, signal.SIG_IGN)
signal.signal(signal.SIGUSR2, signal.SIG_IGN)
if '--shell' in sys.argv:
    index = sys.argv.index('--shell') + 2
    env = {**os.environ, 'SSH_ORIGINAL_COMMAND': ' '.join(sys.argv[index:])}
    signal.signal(signal.SIGUSR1, signal.SIG_DFL)
    signal.signal(signal.SIGUSR2, signal.SIG_DFL)
    wrapper, root = env['RRSYNC_WRAPPER'], env['RRSYNC_ROOT']
    os.execve(wrapper, [wrapper, '-wo', root], env)

from harness.rsync import (
    RSYNC, SCRATCHDIR, makepath, patched_rrsync, proc_self_fd_pins, rmtree, rsync_argv, rsync_path_arg, test_fail,
)

T = 1234567890

base = SCRATCHDIR / 'rrsync-alt-dest-inband-pivot'
rmtree(base)
src = base / 'src'
root = base / 'restricted'
outside = base / 'outside'
makepath(src, root, outside)

(outside / 'f0').write_bytes(b'ADMIN-SECRET\n')
(src / 'f0').write_bytes(b'CLIENT-DATA0\n')
os.symlink('../outside', src / 'basis')
filler = src / 'c'
filler.mkdir()
for i in range(3000):
    (filler / ('%04d' % i)).write_bytes(b'.')
os.utime(src / 'f0', (T, T))
os.utime(outside / 'f0', (T, T))

shim = base / 'rsync-shim'
shim.write_text('#!/bin/sh\nexec ' + rsync_path_arg(RSYNC) + ' "$@"\n')
shim.chmod(0o755)
wrapper = patched_rrsync(base, rsync_path=str(shim))
rsh = f'{shlex.quote(sys.executable)} {shlex.quote(os.path.abspath(__file__))} --shell'
env = {**os.environ, 'RRSYNC_WRAPPER': str(wrapper), 'RRSYNC_ROOT': str(root)}

got = subprocess.run(
    rsync_argv('-rlt', '--copy-dest=basis', '-e', rsh, f'{src}/', 'ignored:'),
    env=env, capture_output=True, text=True, timeout=60)

landed = (root / 'f0').read_bytes() if (root / 'f0').exists() else None
pivot = os.readlink(root / 'basis') if (root / 'basis').is_symlink() else None
print(f'rc={got.returncode}, landed={landed!r}, pivot={pivot!r}, '
      f'stderr={got.stderr!r}', flush=True)
if landed == b'ADMIN-SECRET\n':
    test_fail('--copy-dest read outside the restricted directory through an uploaded symlink')

if proc_self_fd_pins():
    if pivot != '../outside':
        test_fail(f'control did not install the basis pivot: {pivot!r}')
    if got.returncode != 0:
        test_fail(f'the transfer failed instead of finding an empty basis: rc={got.returncode}, stderr={got.stderr!r}')
    if landed != b'CLIENT-DATA0\n':
        test_fail(f'the source file did not transfer: landed={landed!r}')
else:
    if not (got.returncode != 0 and 'receiver option path does not exist' in got.stderr):
        test_fail(f'a missing alt-dest was neither pinned nor refused: rc={got.returncode}, stderr={got.stderr!r}')
    if not (pivot is None and landed is None):
        test_fail(f'the refusal did not stop the transfer: pivot={pivot!r}, landed={landed!r}')

makepath(root / 'real-basis')
(root / 'real-basis' / 'f1').write_bytes(b'BASIS-MATCH\n')
(src / 'f1').write_bytes(b'BASIS-MATCH\n')
os.utime(src / 'f1', (T, T))
os.utime(root / 'real-basis' / 'f1', (T, T))
ok = subprocess.run(
    rsync_argv('-rlt', '--copy-dest=real-basis', '-e', rsh,
               f'{src}/f1', 'ignored:'),
    env=env, capture_output=True, text=True, timeout=20)
if not (ok.returncode == 0 and (root / 'f1').read_bytes() == b'BASIS-MATCH\n'):
    test_fail(f'existing --copy-dest basis failed: rc={ok.returncode}, stderr={ok.stderr!r}')

rmtree(root)
makepath(root)
got = subprocess.run(
    rsync_argv('-rlt', '--compare-dest=basis', '-e', rsh, f'{src}/', 'ignored:'),
    env=env, capture_output=True, text=True, timeout=60)
landed = (root / 'f0').read_bytes() if (root / 'f0').exists() else None
pivot = os.readlink(root / 'basis') if (root / 'basis').is_symlink() else None
if proc_self_fd_pins():
    if not (pivot == '../outside' and got.returncode == 0):
        test_fail(f'compare-dest control failed: pivot={pivot!r}, rc={got.returncode}, stderr={got.stderr!r}')
    if landed != b'CLIENT-DATA0\n':
        test_fail(f'--compare-dest consulted an outside basis: landed={landed!r}, stderr={got.stderr!r}')
else:
    if not (got.returncode != 0 and 'receiver option path does not exist' in got.stderr):
        test_fail(f'a missing --compare-dest was neither pinned nor refused: rc={got.returncode}, stderr={got.stderr!r}')
    if pivot is not None:
        test_fail(f'the refusal did not stop the transfer: pivot={pivot!r}')

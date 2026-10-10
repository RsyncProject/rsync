#!/usr/bin/env python3

import os
import shlex
import signal
import subprocess
import sys

signal.signal(signal.SIGUSR1, signal.SIG_IGN)
signal.signal(signal.SIGUSR2, signal.SIG_IGN)
if '--shell' in sys.argv:
    i = sys.argv.index('--shell') + 2
    env = {**os.environ, 'SSH_ORIGINAL_COMMAND': ' '.join(sys.argv[i:])}
    signal.signal(signal.SIGUSR1, signal.SIG_DFL)
    signal.signal(signal.SIGUSR2, signal.SIG_DFL)
    wrapper, root = env['RRSYNC_WRAPPER'], env['RRSYNC_ROOT']
    os.execve(wrapper, [wrapper, '-wo', '-no-lock', root], env)

from harness.rsync import (
    RSYNC, SCRATCHDIR, makepath, patched_rrsync, rmtree, rsync_argv, rsync_path_arg, test_fail,
)

base = SCRATCHDIR / 'rrsync-write-only-files-from'
rmtree(base)
src = base / 'src'
root = base / 'root'
makepath(src, root)
secret_name = 'SERVER-ONLY-SECRET-NAME'
(root / 'protected-list').write_text(secret_name + '\n')

shim = base / 'rsync-shim'
shim.write_text('#!/bin/sh\nexec ' + rsync_path_arg(RSYNC) + ' "$@"\n')
shim.chmod(0o755)
wrapper = patched_rrsync(base, rsync_path=str(shim))
rsh = f'{shlex.quote(sys.executable)} {shlex.quote(os.path.abspath(__file__))} --shell'
env = {**os.environ, 'RRSYNC_WRAPPER': str(wrapper), 'RRSYNC_ROOT': str(root)}
got = subprocess.run(
    rsync_argv('-r', '--files-from=:protected-list',
               '-e', rsh, f'{src}/', 'ignored:'),
    env=env, capture_output=True, text=True, timeout=20)

if not (got.returncode != 0 and 'write-only server cannot read' in got.stderr):
    test_fail(f'write-only rrsync accepted remote --files-from: rc={got.returncode}, stdout={got.stdout!r}, stderr={got.stderr!r}')
if not (secret_name not in got.stdout and secret_name not in got.stderr):
    test_fail(f'write-only rrsync returned the protected record: rc={got.returncode}, stdout={got.stdout!r}, stderr={got.stderr!r}')

(src / 'allowed').write_bytes(b'NEW')
(src / 'unlisted').write_bytes(b'NOPE')
local_list = base / 'local-list'
local_list.write_text('allowed\n')
safe = subprocess.run(
    rsync_argv('-r', f'--files-from={local_list}',
               '-e', rsh, f'{src}/', 'ignored:'),
    env=env, capture_output=True, text=True, timeout=20)
if not (safe.returncode == 0 and (root / 'allowed').read_bytes() == b'NEW'):
    test_fail(f'local --files-from upload failed: rc={safe.returncode}, stdout={safe.stdout!r}, stderr={safe.stderr!r}')
if (root / 'unlisted').exists():
    test_fail('--files-from uploaded an unlisted file')

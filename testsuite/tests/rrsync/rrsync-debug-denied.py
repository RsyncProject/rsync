#!/usr/bin/env python3

import os
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

from harness.rsync import RSYNC, SCRATCHDIR, makepath, patched_rrsync, rmtree, rsync_argv, test_fail

base = SCRATCHDIR / 'rrsync-debug-denied'
rmtree(base)
src = base / 'src'
root = base / 'root'
makepath(src, root)
(src / 'f').write_bytes(b'hi\n')

shim = base / 'rsync-shim'
shim.write_text('#!/bin/sh\nexec ' + RSYNC + ' "$@"\n')
shim.chmod(0o755)
wrapper = patched_rrsync(base, rsync_path=str(shim))
rsh = f'{sys.executable} {os.path.abspath(__file__)} --shell'
env = {**os.environ, 'RRSYNC_WRAPPER': str(wrapper), 'RRSYNC_ROOT': str(root)}

for spelling in ('-M--debug=FILTER2', '--remote-option=--debug=ALL3'):
    got = subprocess.run(
        rsync_argv('-r', spelling, '-e', rsh, f'{src}/', 'ignored:'),
        env=env, capture_output=True, text=True, timeout=20)
    out = got.stdout + got.stderr
    if not (got.returncode != 0 and 'option --debug has been disabled' in out):
        test_fail(f'rrsync accepted a peer-selected {spelling}: rc={got.returncode}, out={out!r}')

if "'debug': -1," not in wrapper.read_text():
    test_fail("support/rrsync no longer disables 'debug' -- a cull-options regeneration probably restored it")

for extra in ([], ['-vv'], ['-vvv']):
    ok = subprocess.run(
        rsync_argv('-r', *extra, '-e', rsh, f'{src}/', 'ignored:'),
        env=env, capture_output=True, text=True, timeout=20)
    if not (ok.returncode == 0 and (root / 'f').read_bytes() == b'hi\n'):
        test_fail(f'an ordinary transfer broke with {extra}: rc={ok.returncode}, stderr={ok.stderr!r}')

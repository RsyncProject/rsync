#!/usr/bin/env python3
"""rrsync -no-overwrite must protect existing partial-dir files too."""

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
    flags = env.get('RRSYNC_FLAGS', '-wo -no-overwrite -no-lock').split()
    os.execve(wrapper, [wrapper, *flags, root], env)

from rsyncfns import RSYNC, SCRATCHDIR, makepath, patched_rrsync, rmtree, rsync_argv, rsync_path_arg

base = SCRATCHDIR / 'rrsync-no-overwrite-partial-dir'
rmtree(base)
src = base / 'src'
root = base / 'root'
makepath(src, root / 'protected')
(src / 'victim').write_bytes(b'NEW')
protected = root / 'protected' / 'victim'
protected.write_bytes(b'POLICY')

# RSYNC may be a multi-word command (the runner's --protocol=N, or valgrind)
# while rrsync hands its RSYNC to execlp() as a single executable name, so it
# has to be wrapped before patched_rrsync() sees it.
shim = base / 'rsync-shim'
shim.write_text('#!/bin/sh\nexec ' + rsync_path_arg(RSYNC) + ' "$@"\n')
shim.chmod(0o755)
wrapper = patched_rrsync(base, rsync_path=str(shim))
rsh = f'{shlex.quote(sys.executable)} {shlex.quote(os.path.abspath(__file__))} --shell'
env = {**os.environ, 'RRSYNC_WRAPPER': str(wrapper), 'RRSYNC_ROOT': str(root)}
got = subprocess.run(
    rsync_argv('-rI', '--partial', '--partial-dir=protected',
               '-e', rsh, f'{src}/victim', 'ignored:'),
    env=env, capture_output=True, text=True, timeout=20)

state = protected.read_bytes() if protected.exists() else None
assert got.returncode != 0 and 'option --partial-dir has been disabled' in got.stderr, (
    f'rrsync -no-overwrite accepted --partial-dir: rc={got.returncode}, '
    f'stdout={got.stdout!r}, stderr={got.stderr!r}')
assert state == b'POLICY', (
    f'rrsync consumed an existing partial file: rc={got.returncode}, '
    f'state={state!r}, stderr={got.stderr!r}')

safe = subprocess.run(
    rsync_argv('-rI', '-e', rsh, f'{src}/victim', 'ignored:'),
    env=env, capture_output=True, text=True, timeout=20)
assert safe.returncode == 0 and (root / 'victim').read_bytes() == b'NEW', (
    f'ordinary upload failed: rc={safe.returncode}, stderr={safe.stderr!r}')

# The refusal must be CONDITIONAL on -no-overwrite.  Run the identical option
# through a wrapper without it: if that is refused too, the option is simply
# disabled for every rrsync deployment and the assertion above proves nothing
# about the policy.  A fresh root keeps this from disturbing the checks above.
plain_root = base / 'root-plain'
rmtree(plain_root)
makepath(plain_root / 'protected')
(plain_root / 'protected' / 'victim').write_bytes(b'POLICY')
plain_env = {**env, 'RRSYNC_ROOT': str(plain_root), 'RRSYNC_FLAGS': '-wo -no-lock'}
plain = subprocess.run(
    rsync_argv('-rI', '--partial', '--partial-dir=protected', '-e', rsh, f'{src}/victim', 'ignored:'),
    env=plain_env, capture_output=True, text=True, timeout=20)
assert plain.returncode == 0, (
    'without -no-overwrite the same option must be accepted, or the refusal '
    'above is unconditional and says nothing about the policy: '
    f'rc={plain.returncode}, stderr={plain.stderr!r}')

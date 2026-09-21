#!/usr/bin/env python3
"""A forced rrsync -wo endpoint must not return a local --files-from file."""

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

from rsyncfns import RSYNC, SCRATCHDIR, makepath, patched_rrsync, rmtree, rsync_argv, rsync_path_arg

base = SCRATCHDIR / 'rrsync-write-only-files-from'
rmtree(base)
src = base / 'src'
root = base / 'root'
makepath(src, root)
secret_name = 'SERVER-ONLY-SECRET-NAME'
(root / 'protected-list').write_text(secret_name + '\n')

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
    rsync_argv('-r', '--files-from=:protected-list',
               '-e', rsh, f'{src}/', 'ignored:'),
    env=env, capture_output=True, text=True, timeout=20)

assert got.returncode != 0 and 'write-only server cannot read' in got.stderr, (
    f'write-only rrsync accepted remote --files-from: rc={got.returncode}, '
    f'stdout={got.stdout!r}, stderr={got.stderr!r}')
assert secret_name not in got.stdout and secret_name not in got.stderr, (
    f'write-only rrsync returned the protected record: rc={got.returncode}, '
    f'stdout={got.stdout!r}, stderr={got.stderr!r}')

# Control: a CLIENT-local --files-from upload must still work.  A second,
# unlisted source file makes this prove list selection as well: with only one
# file present an ordinary recursive upload would look identical, so the
# control would pass even if the list were ignored entirely.
(src / 'allowed').write_bytes(b'NEW')
(src / 'unlisted').write_bytes(b'NOPE')
local_list = base / 'local-list'
local_list.write_text('allowed\n')
safe = subprocess.run(
    rsync_argv('-r', f'--files-from={local_list}',
               '-e', rsh, f'{src}/', 'ignored:'),
    env=env, capture_output=True, text=True, timeout=20)
assert safe.returncode == 0 and (root / 'allowed').read_bytes() == b'NEW', (
    f'local --files-from upload failed: rc={safe.returncode}, '
    f'stdout={safe.stdout!r}, stderr={safe.stderr!r}')
assert not (root / 'unlisted').exists(), (
    'the client-local list was not honoured: an unlisted file was uploaded, '
    'so this control does not show that --files-from selected anything')

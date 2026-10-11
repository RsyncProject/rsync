#!/usr/bin/env python3
"""rrsync accepts --delay-symlinks, and -no-overwrite refuses it.

The deferred symlinks are created at the end of the transfer, replacing
whatever has appeared at that name since the --ignore-existing check, so
-no-overwrite has to refuse the option as it does --delay-updates."""

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

from rsyncfns import (
    RSYNC, SCRATCHDIR, forced_protocol, makepath, patched_rrsync, rmtree,
    rsync_argv, rsync_path_arg, test_skipped,
)

proto = forced_protocol()
if proto is not None and proto < 29:
    test_skipped(f"--delay-symlinks requires protocol 29+ (negotiated {proto})")

base = SCRATCHDIR / 'rrsync-no-overwrite-delay-symlinks'
rmtree(base)
src = base / 'src'
makepath(src)
(src / 'target').write_bytes(b'NEW')
os.symlink('target', src / 'link')

# RSYNC may be a multi-word command (the runner's --protocol=N, or valgrind)
# while rrsync hands its RSYNC to execlp() as a single executable name, so it
# has to be wrapped before patched_rrsync() sees it.
shim = base / 'rsync-shim'
shim.write_text('#!/bin/sh\nexec ' + rsync_path_arg(RSYNC) + ' "$@"\n')
shim.chmod(0o755)
wrapper = patched_rrsync(base, rsync_path=str(shim))
rsh = f'{shlex.quote(sys.executable)} {shlex.quote(os.path.abspath(__file__))} --shell'


def push(root, flags):
    rmtree(root)
    makepath(root)
    env = {**os.environ, 'RRSYNC_WRAPPER': str(wrapper), 'RRSYNC_ROOT': str(root),
           'RRSYNC_FLAGS': flags}
    return subprocess.run(
        rsync_argv('-rl', '--delay-symlinks', '-e', rsh, f'{src}/', 'ignored:'),
        env=env, capture_output=True, text=True, timeout=20)


root = base / 'root'
got = push(root, '-wo -no-overwrite -no-lock')
assert got.returncode != 0 and 'option --delay-symlinks has been disabled' in got.stderr, (
    f'rrsync -no-overwrite accepted --delay-symlinks: rc={got.returncode}, '
    f'stdout={got.stdout!r}, stderr={got.stderr!r}')
assert not os.listdir(root), f'a refused transfer wrote into the root: {os.listdir(root)}'

# The refusal must be CONDITIONAL on -no-overwrite, and an ordinary rrsync
# must accept the option: run the same push through a wrapper without it.
plain_root = base / 'root-plain'
plain = push(plain_root, '-wo -no-lock')
assert plain.returncode == 0, (
    'without -no-overwrite rrsync must accept --delay-symlinks: '
    f'rc={plain.returncode}, stderr={plain.stderr!r}')
assert os.readlink(plain_root / 'link') == 'target', 'the symlink was not transferred'
assert (plain_root / 'target').read_bytes() == b'NEW', 'the target was not transferred'

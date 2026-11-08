#!/usr/bin/env python3
"""rrsync -log must record every transfer in the admin's log file.

The client's own --log-file is refused while -log is in force.
"""

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
    flags = shlex.split(env['RRSYNC_FLAGS'])
    os.execve(wrapper, [wrapper, *flags, root], env)

from rsyncfns import RSYNC, SCRATCHDIR, makepath, patched_rrsync, rmtree, rsync_argv, rsync_path_arg

base = SCRATCHDIR / 'rrsync-log'
rmtree(base)
src = base / 'src'
root = base / 'root'
makepath(src / 'sub', root)
(src / 'a.txt').write_bytes(b'AAA')
(src / 'sub' / 'b.txt').write_bytes(b'BB')
log = base / 'transfer.log'

# RSYNC may be a multi-word command (the runner's --protocol=N, or valgrind)
# while rrsync hands its RSYNC to execlp() as a single executable name, so it
# has to be wrapped before patched_rrsync() sees it.
shim = base / 'rsync-shim'
shim.write_text('#!/bin/sh\nexec ' + rsync_path_arg(RSYNC) + ' "$@"\n')
shim.chmod(0o755)
wrapper = patched_rrsync(base, rsync_path=str(shim))
rsh = f'{shlex.quote(sys.executable)} {shlex.quote(os.path.abspath(__file__))} --shell'
env = {**os.environ, 'RRSYNC_WRAPPER': str(wrapper), 'RRSYNC_ROOT': str(root),
       'RRSYNC_FLAGS': f'-wo -no-lock -log {shlex.quote(str(log))}'}

push = subprocess.run(
    rsync_argv('-r', '-e', rsh, f'{src}/', 'ignored:'),
    env=env, capture_output=True, text=True, timeout=20)
assert push.returncode == 0 and (root / 'sub' / 'b.txt').read_bytes() == b'BB', (
    f'upload with -log failed: rc={push.returncode}, stderr={push.stderr!r}')
lines = log.read_text().splitlines() if log.exists() else []
for name in ('a.txt', 'sub/b.txt'):
    assert any('>f' in line and line.endswith(' ' + name) for line in lines), (
        f'-log did not record {name}: {lines!r}')

# The admin's log replaces the client's: a client --log-file is an error, not
# a second log the client can point anywhere inside the restricted dir.
client_log = subprocess.run(
    rsync_argv('-r', '-M--log-file=mine.log', '-e', rsh, f'{src}/', 'ignored:'),
    env=env, capture_output=True, text=True, timeout=20)
assert client_log.returncode != 0 and 'option --log-file has been disabled' in client_log.stderr, (
    f'rrsync -log accepted the client --log-file: rc={client_log.returncode}, '
    f'stderr={client_log.stderr!r}')
assert not (root / 'mine.log').exists()

# Without -log the client keeps its --log-file, or the refusal above is
# unconditional and says nothing about -log.
plain_env = {**env, 'RRSYNC_FLAGS': '-wo -no-lock'}
plain = subprocess.run(
    rsync_argv('-r', '-M--log-file=mine.log', '-e', rsh, f'{src}/', 'ignored:'),
    env=plain_env, capture_output=True, text=True, timeout=20)
assert plain.returncode == 0 and (root / 'mine.log').exists(), (
    f'without -log the client --log-file must be accepted: rc={plain.returncode}, '
    f'stderr={plain.stderr!r}')

#!/usr/bin/env python3
"""rrsync -log must record every transfer in the admin's log file.

The client's own --log-file is refused while -log is in force, and a FILE
the client could redirect (inside the restricted dir, behind a symlink, or in
a bind mount of the restricted dir) is refused outright.
"""

import os
import shlex
import shutil
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

# A log inside the restricted dir could be replaced by a client symlink.
inside_log = shlex.quote(str(root / 'transfer.log'))
inside_env = {**env, 'RRSYNC_FLAGS': f'-wo -no-lock -log {inside_log}'}
inside = subprocess.run(
    rsync_argv('-r', '-e', rsh, f'{src}/', 'ignored:'),
    env=inside_env, capture_output=True, text=True, timeout=20)
assert inside.returncode != 0 and 'must be outside the restricted directory' in inside.stderr, (
    f'rrsync accepted a -log FILE inside DIR: rc={inside.returncode}, stderr={inside.stderr!r}')
assert not (root / 'transfer.log').exists()

# Nor may the path pass through DIR by way of a symlink: the client's own
# symlink in DIR would decide where the log is written.
elsewhere = base / 'elsewhere'
makepath(elsewhere)
os.symlink(elsewhere, root / 'planted')
os.symlink(root / 'planted', base / 'via')
via_log = shlex.quote(str(base / 'via' / 'transfer.log'))
via_env = {**env, 'RRSYNC_FLAGS': f'-wo -no-lock -log {via_log}'}
via = subprocess.run(
    rsync_argv('-r', '-e', rsh, f'{src}/', 'ignored:'),
    env=via_env, capture_output=True, text=True, timeout=20)
assert via.returncode != 0 and 'must not contain a symlink' in via.stderr, (
    f'rrsync accepted a -log path through DIR: rc={via.returncode}, stderr={via.stderr!r}')
assert not (elsewhere / 'transfer.log').exists()

# DIR reached through a bind mount is still DIR.  An unprivileged bind mount
# needs a user namespace, so this part runs only where unshare can make one.
unshare = shutil.which('unshare')
userns = [unshare, '--user', '--map-root-user', '--mount'] if unshare else None
if userns and subprocess.run(userns + ['true'], capture_output=True).returncode == 0:
    bind = base / 'bind'
    makepath(bind)
    script = 'mount --bind "$1" "$2" && exec "$3" -wo -no-lock -log "$2/transfer.log" "$1"'
    bound = subprocess.run(
        userns + ['sh', '-c', script, 'sh', str(root), str(bind), str(wrapper)],
        env={**os.environ, 'SSH_ORIGINAL_COMMAND': 'rsync --server -r . .'},
        stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=20)
    assert bound.returncode != 0 and 'must be outside the restricted directory' in bound.stderr, (
        f'rrsync accepted a -log FILE in a bind mount of DIR: rc={bound.returncode}, '
        f'stderr={bound.stderr!r}')
    assert not (root / 'transfer.log').exists()

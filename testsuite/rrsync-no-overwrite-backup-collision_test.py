#!/usr/bin/env python3
"""rrsync -no-overwrite must not let backup publication overwrite a protected file.

--ignore-existing protects the live destination, but a backup published onto a
name that already exists deletes what is there (backup.c make_backup()), and
deleting a file backs it up first (delete.c).  So a --delete of an unrelated
file can land on a protected name that the source itself also carries.
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
    flags = env.get('RRSYNC_FLAGS', '-wo -no-overwrite -no-lock').split()
    os.execve(wrapper, [wrapper, *flags, root], env)

from rsyncfns import RSYNC, SCRATCHDIR, makepath, patched_rrsync, rmtree, rsync_argv, rsync_path_arg

base = SCRATCHDIR / 'rrsync-no-overwrite-backup-collision'
rmtree(base)
src = base / 'src'
makepath(src)
# In the source too, so --ignore-existing is what is supposed to protect it.
(src / 'victim.bak').write_bytes(b'SOURCE')


def plant(root):
    makepath(root)
    (root / 'victim').write_bytes(b'OLD-LIVE')     # absent from src: --delete removes it
    (root / 'victim.bak').write_bytes(b'POLICY')   # the protected file


root = base / 'root'
plant(root)

# RSYNC may be a multi-word command (the runner's --protocol=N, or valgrind)
# while rrsync hands its RSYNC to execlp() as a single executable name, so it
# has to be wrapped before patched_rrsync() sees it.
shim = base / 'rsync-shim'
shim.write_text('#!/bin/sh\nexec ' + rsync_path_arg(RSYNC) + ' "$@"\n')
shim.chmod(0o755)
wrapper = patched_rrsync(base, rsync_path=str(shim))
rsh = f'{shlex.quote(sys.executable)} {shlex.quote(os.path.abspath(__file__))} --shell'
env = {**os.environ, 'RRSYNC_WRAPPER': str(wrapper), 'RRSYNC_ROOT': str(root)}

# The stock client sends -b inside the remote short-option bundle, so the
# refusal has to come from short_disabled, not the long-option table.
argv = rsync_argv('-r', '--delete', '--backup', '--suffix=.bak', '-e', rsh, f'{src}/', 'ignored:')
got = subprocess.run(argv, env=env, capture_output=True, text=True, timeout=20)

state = (root / 'victim.bak').read_bytes() if (root / 'victim.bak').exists() else None
assert got.returncode != 0 and 'option -b has been disabled' in got.stderr, (
    f'rrsync -no-overwrite accepted backup mode: rc={got.returncode}, '
    f'stdout={got.stdout!r}, stderr={got.stderr!r}')
assert state == b'POLICY', (
    f'backup publication overwrote a protected file: state={state!r}, '
    f'stderr={got.stderr!r}')

# An ordinary upload must still work.
(src / 'new').write_bytes(b'NEW')
safe = subprocess.run(
    rsync_argv('-rI', '-e', rsh, f'{src}/new', 'ignored:'),
    env=env, capture_output=True, text=True, timeout=20)
assert safe.returncode == 0 and (root / 'new').read_bytes() == b'NEW', (
    f'ordinary upload failed: rc={safe.returncode}, stderr={safe.stderr!r}')

# The refusal must be CONDITIONAL on -no-overwrite -- and the identical run
# through a wrapper without it also demonstrates that the destructive primitive
# is real, which the assertions above cannot show once the refusal aborts the
# transfer.  --ignore-existing is passed by hand here: it is what -no-overwrite
# forces, so this shows the collision defeating that protection rather than the
# transfer simply writing its own victim.bak.  A fresh root keeps this from
# disturbing the checks above.
plain_root = base / 'root-plain'
rmtree(plain_root)
plant(plain_root)
plain_env = {**env, 'RRSYNC_ROOT': str(plain_root), 'RRSYNC_FLAGS': '-wo -no-lock'}
plain_argv = rsync_argv('-r', '--delete', '--backup', '--suffix=.bak',
                        '--ignore-existing', '-e', rsh, f'{src}/', 'ignored:')
plain = subprocess.run(plain_argv, env=plain_env, capture_output=True, text=True, timeout=20)
plain_state = (plain_root / 'victim.bak').read_bytes() if (plain_root / 'victim.bak').exists() else None
assert plain.returncode == 0, (
    'without -no-overwrite the same options must be accepted, or the refusal '
    'above is unconditional and says nothing about the policy: '
    f'rc={plain.returncode}, stderr={plain.stderr!r}')
assert plain_state == b'OLD-LIVE', (
    'the backup collision did not overwrite the protected file even without '
    '-no-overwrite, so this test no longer exercises the primitive it claims '
    f'to: state={plain_state!r}, stderr={plain.stderr!r}')

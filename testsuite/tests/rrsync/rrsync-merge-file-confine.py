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
    os.execve(wrapper, [wrapper, '-ro', '-no-lock', root], env)

from harness.rsync import (
    RSYNC, SCRATCHDIR, makepath, patched_rrsync, rmtree, rsync_argv, rsync_path_arg, test_fail,
)

base = SCRATCHDIR / 'rrsync-merge-file-confine'
rmtree(base)
root = base / 'root'
sub = root / 'sub'
dest = base / 'dest'
makepath(sub, dest)

(base / 'outside-secret').write_text('secretword\n')
(root / 'secretword').write_bytes(b'X')
(root / 'keep').write_bytes(b'X')
(sub / 'intree-dropped').write_bytes(b'X')
(sub / 'intree-kept').write_bytes(b'X')
(sub / '.rsync-filter').write_text('intree-dropped\n')

shim = base / 'rsync-shim'
shim.write_text('#!/bin/sh\nexec ' + rsync_path_arg(RSYNC) + ' "$@"\n')
shim.chmod(0o755)
wrapper = patched_rrsync(base, rsync_path=str(shim))
rsh = f'{shlex.quote(sys.executable)} {shlex.quote(os.path.abspath(__file__))} --shell'
env = {**os.environ, 'RRSYNC_WRAPPER': str(wrapper), 'RRSYNC_ROOT': str(root)}

def pull(filt, into):
    into.mkdir(parents=True, exist_ok=True)
    return subprocess.run(
        rsync_argv('-r', f'--filter={filt}', '-e', rsh, 'ignored:.', f'{into}/'),
        env=env, capture_output=True, text=True, timeout=20)

esc = dest / 'escape'
got = pull(':-s ../outside-secret', esc)
if got.returncode != 0:
    test_fail(f'confined merge transfer failed: rc={got.returncode}, stderr={got.stderr!r}')
if not ((esc / 'secretword').exists()):
    test_fail(f'outside merge file excluded secretword: stdout={got.stdout!r}, stderr={got.stderr!r}')
if 'secretword' in got.stderr:
    test_fail(f"the out-of-root merge file's text reached the peer: stderr={got.stderr!r}")

ctl = dest / 'control'
got = pull(':-s .rsync-filter', ctl)
if got.returncode != 0:
    test_fail(f'the in-tree dir-merge failed the transfer: rc={got.returncode}, stderr={got.stderr!r}')
if not ((ctl / 'sub' / 'intree-kept').exists()):
    test_fail(f'the in-tree dir-merge broke the transfer: rc={got.returncode}, stdout={got.stdout!r}, stderr={got.stderr!r}')
if (ctl / 'sub' / 'intree-dropped').exists():
    test_fail(f'in-tree merge file was not applied: stdout={got.stdout!r}, stderr={got.stderr!r}')

alias_root = base / 'aliased'
makepath(alias_root)
(base / 'aliased-secret').write_text('aliased-word\n')
(alias_root / 'aliased-word').write_bytes(b'X')
(alias_root / 'plain').write_bytes(b'X')
os.symlink('.', alias_root / 'self-alias')
os.symlink('../aliased-secret', alias_root / 'outside-link')
(alias_root / 'intree-list').write_text('plain\n')

def confined_pull(filter_text, into):
    (alias_root / '.rsync-filter').write_text(filter_text + '\n')
    rmtree(into)
    into.mkdir(parents=True, exist_ok=True)
    return subprocess.run(
        rsync_argv('-r', f'--confine-root={alias_root}',
                   '--filter=:s .rsync-filter',
                   f'{alias_root}/self-alias/', f'{into}/'),
        capture_output=True, text=True, timeout=20)

esc2 = dest / 'aliased-escape'
got = confined_pull('merge,- outside-link', esc2)
if got.returncode == 0 and (not (esc2 / 'aliased-word').exists()):
    test_fail(f'symlinked source read an outside merge file: stderr={got.stderr!r}')

ctl2 = dest / 'aliased-control'
got = confined_pull('merge,- intree-list', ctl2)
if not (got.returncode == 0 and (ctl2 / 'aliased-word').exists()):
    test_fail(f'a symlinked source arg with an in-tree merge file broke the transfer: rc={got.returncode}, stderr={got.stderr!r}')
if (ctl2 / 'plain').exists():
    test_fail(f'symlinked source ignored its in-tree merge file: stderr={got.stderr!r}')

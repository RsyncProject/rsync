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
    wrapper = env['RRSYNC_WRAPPER']
    root = env['RRSYNC_ROOT']
    os.execve(wrapper, [wrapper, '-wo', root], env)

from harness.rsync import (
    RSYNC, SCRATCHDIR, makepath, patched_rrsync, proc_self_fd_pins, rmtree, rsync_argv, rsync_path_arg, test_fail,
)

PINS = proc_self_fd_pins()

base = SCRATCHDIR / 'rrsync-backup-dir-inband-pivot'
rmtree(base)
stage = base / 'attacker-stage'
src = base / 'attacker-next'
root = base / 'restricted'
outside = base / 'outside'
makepath(stage, src, root, outside)

(stage / 'authorized_keys').write_bytes(b'ATTACKER-SSH-KEY\n')
(src / 'authorized_keys').write_bytes(b'NEW-IN-TREE-CONTENT\n')
os.symlink('../outside', src / 'a')
(outside / 'authorized_keys').write_bytes(b'ADMIN-SSH-KEY\n')

shim = base / 'rsync-shim'
shim.write_text('#!/bin/sh\nexec ' + rsync_path_arg(RSYNC) + ' "$@"\n')
shim.chmod(0o755)
wrapper = patched_rrsync(base, rsync_path=str(shim))
rsh = f'{shlex.quote(sys.executable)} {shlex.quote(os.path.abspath(__file__))} --shell'
env = {**os.environ, 'RRSYNC_WRAPPER': str(wrapper), 'RRSYNC_ROOT': str(root)}

seed = subprocess.run(
    rsync_argv('-rlI', '-e', rsh, f'{stage}/authorized_keys', 'ignored:'),
    env=env, capture_output=True, text=True, timeout=20)
if seed.returncode != 0:
    test_fail(f'seed upload failed: {seed.stderr}')
if (root / 'authorized_keys').read_bytes() != b'ATTACKER-SSH-KEY\n':
    test_fail('seed upload wrote the wrong content')
if (root / 'a').exists():
    test_fail('seed upload unexpectedly created the pivot path')

os.symlink('../outside', root / 'a')
blocked = subprocess.run(
    rsync_argv('-rlbI', '--backup-dir=a', '-e', rsh,
               f'{src}/', 'ignored:'),
    env=env, capture_output=True, text=True, timeout=20)
if blocked.returncode == 0:
    test_fail('pre-existing backup symlink was accepted')
if (outside / 'authorized_keys').read_bytes() != b'ADMIN-SSH-KEY\n':
    test_fail('blocked backup changed the outside file')
(root / 'a').unlink()

seed = subprocess.run(
    rsync_argv('-rlI', '-e', rsh, f'{stage}/authorized_keys', 'ignored:'),
    env=env, capture_output=True, text=True, timeout=20)
if seed.returncode != 0:
    test_fail(f'second seed upload failed: {seed.stderr}')

got = subprocess.run(
    rsync_argv('-rlbI', '--backup-dir=a', '-e', rsh,
               f'{src}/', 'ignored:'),
    env=env, capture_output=True, text=True, timeout=20)

escaped = outside / 'authorized_keys'
pivot = os.readlink(root / 'a') if (root / 'a').is_symlink() else None
print(f'seed_rc={seed.returncode}, rc={got.returncode}, pivot={pivot!r}, '
      f'escaped={escaped.read_bytes()!r}, stderr={got.stderr!r}', flush=True)
if escaped.read_bytes() != b'ADMIN-SSH-KEY\n':
    test_fail('--backup-dir replaced an outside file through an uploaded symlink')
if got.returncode == 0:
    test_fail('--backup-dir pivot was neither pinned nor refused')
if PINS:
    if pivot != '../outside':
        test_fail(f'the transfer did not plant the pivot symlink, so the run proves nothing: pivot={pivot!r}')
    if '/proc/self/fd/' not in got.stderr:
        test_fail(f'pinned backup path produced the wrong error: {got.stderr!r}')
else:
    if 'receiver option path does not exist' not in got.stderr:
        test_fail(f'missing backup path produced the wrong error: {got.stderr!r}')

tmpdir = subprocess.run(
    rsync_argv('-rlI', '--temp-dir=no-such-tmp', '-e', rsh,
               f'{src}/authorized_keys', 'ignored:'),
    env=env, capture_output=True, text=True, timeout=20)
if not (tmpdir.returncode != 0 and 'receiver option path does not exist' in tmpdir.stderr):
    test_fail(f'a missing --temp-dir was not refused: rc={tmpdir.returncode}, stderr={tmpdir.stderr!r}')
if (root / 'no-such-tmp').exists():
    test_fail('a missing --temp-dir must not be created either')

if not PINS:
    for opt in ('--backup-dir=fresh-backup', '--partial-dir=.rsync-partial',
                '--link-dest=no-such-basis'):
        r = subprocess.run(
            rsync_argv('-rlbI', opt, '-e', rsh, f'{src}/authorized_keys', 'ignored:'),
            env=env, capture_output=True, text=True, timeout=20)
        if not (r.returncode != 0 and 'receiver option path does not exist' in r.stderr):
            test_fail(f'{opt} was neither pinned nor refused without /proc/self/fd: rc={r.returncode}, stderr={r.stderr!r}')
    sys.exit(0)

def reseed():
    r = subprocess.run(
        rsync_argv('-rlI', '-e', rsh, f'{stage}/authorized_keys', 'ignored:'),
        env=env, capture_output=True, text=True, timeout=20)
    if r.returncode != 0:
        test_fail(f'reseed failed: stderr={r.stderr!r}')
    if (root / 'authorized_keys').read_bytes() != b'ATTACKER-SSH-KEY\n':
        test_fail('reseed wrote the wrong content')

reseed()

(root / 'backup').mkdir()
normal = subprocess.run(
    rsync_argv('-rlbI', '--backup-dir=backup', '-e', rsh,
               f'{src}/authorized_keys', 'ignored:'),
    env=env, capture_output=True, text=True, timeout=20)
if normal.returncode != 0:
    test_fail(f'ordinary in-tree backup failed: stderr={normal.stderr!r}')
if (root / 'backup' / 'authorized_keys').read_bytes() != b'ATTACKER-SSH-KEY\n':
    test_fail('ordinary backup wrote the wrong content')

reseed()
firstuse = subprocess.run(
    rsync_argv('-rlbI', '--backup-dir=fresh-backup', '-e', rsh,
               f'{src}/authorized_keys', 'ignored:'),
    env=env, capture_output=True, text=True, timeout=20)
if firstuse.returncode != 0:
    test_fail(f'a first-use --backup-dir was refused: rc={firstuse.returncode}, stderr={firstuse.stderr!r}')
fresh = root / 'fresh-backup'
if not (fresh.is_dir() and (not fresh.is_symlink())):
    test_fail(f'first-use --backup-dir did not leave a real directory: {fresh}')
if (fresh / 'authorized_keys').read_bytes() != b'ATTACKER-SSH-KEY\n':
    test_fail('the backup did not land in the created backup dir, so the run above proves nothing about --backup-dir working')

reseed()
nested = subprocess.run(
    rsync_argv('-rlbI', '--backup-dir=bak/2026/07', '-e', rsh,
               f'{src}/authorized_keys', 'ignored:'),
    env=env, capture_output=True, text=True, timeout=20)
if nested.returncode != 0:
    test_fail(f'a nested first-use --backup-dir was refused: rc={nested.returncode}, stderr={nested.stderr!r}')
if not ((root / 'bak' / '2026' / '07' / 'authorized_keys').exists()):
    test_fail('the nested backup dir was accepted but the backup did not land in it')

partial = subprocess.run(
    rsync_argv('-rlI', '--partial', '--partial-dir=.rsync-partial', '-e', rsh,
               f'{src}/authorized_keys', 'ignored:'),
    env=env, capture_output=True, text=True, timeout=20)
if partial.returncode != 0:
    test_fail(f'a first-use --partial-dir was refused: rc={partial.returncode}, stderr={partial.stderr!r}')
pdir = root / '.rsync-partial'
if not (pdir.is_dir() and (not pdir.is_symlink())):
    test_fail(f'first-use --partial-dir did not leave a real directory: {pdir}')
mode = pdir.stat().st_mode & 0o777
if mode != 448:
    test_fail(f'the partial dir was created {mode:04o}, not 0700: partial files would be readable by other users on the server')

altdest = subprocess.run(
    rsync_argv('-rlI', '--link-dest=no-such-basis', '-e', rsh,
               f'{src}/authorized_keys', 'ignored:'),
    env=env, capture_output=True, text=True, timeout=20)
if altdest.returncode != 0:
    test_fail(f'a first-run --link-dest was refused: rc={altdest.returncode}, stderr={altdest.stderr!r}')
if (root / 'no-such-basis').exists():
    test_fail('a missing --link-dest must not be created')
if list(root.glob('.rrsync-empty-basis*')):
    test_fail('the basis placeholder was left behind in the restricted dir')

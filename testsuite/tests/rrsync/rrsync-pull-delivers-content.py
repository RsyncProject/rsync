#!/usr/bin/env python3

import os
import shlex
import subprocess

from harness.rsync import (
    RSYNC, SCRATCHDIR, makepath, patched_rrsync, rmtree, rsync_argv, test_fail, rsync_path_arg,
)

PULL_DATA = 'REAL-FILE-CONTENT\n'
PUSH_DATA = 'PUSHED-FILE-CONTENT\n'

base = SCRATCHDIR / 'rrsync-pull-content'
rmtree(base)
restricted = base / 'restricted'
dest = base / 'dest'
src = base / 'src'
outside = base / 'outside'
makepath(restricted, dest, src, outside)

(restricted / 'f1').write_text(PULL_DATA)
(restricted / 'f2').write_text(PULL_DATA)
(src / 'up1').write_text(PUSH_DATA)
makepath(restricted / 'inbox')
(outside / 'list').write_text('f1\n')

shim = base / 'rsync-shim'
shim.write_text('#!/bin/sh\nexec ' + rsync_path_arg(RSYNC) + ' "$@"\n')
shim.chmod(0o755)

rrsync = patched_rrsync(base, rsync_path=str(shim))

rsh = base / 'fake-rsh'
rsh.write_text(
    '#!/bin/sh\n'
    'shift\n'
    'SSH_ORIGINAL_COMMAND="$*"\n'
    'export SSH_ORIGINAL_COMMAND\n'
    'exec %s %s\n' % (shlex.quote(str(rrsync)), shlex.quote(str(restricted))))
rsh.chmod(0o755)

def transfer(*args):
    return subprocess.run(rsync_argv('-a', '-e', str(rsh), *args),
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          text=True)

proc = transfer('dummy:f1', str(dest) + '/')
ctx = f'rc={proc.returncode}, output={proc.stdout.strip()[:300]!r}'
got = dest / 'f1'

if got.is_symlink():
    test_fail(f'pull delivered a symlink instead of the file: '
              f'{os.readlink(got)!r} ({ctx})')
if not got.exists():
    test_fail(f'pull delivered nothing ({ctx})')
if not got.is_file():
    test_fail(f'pull delivered a non-regular file ({ctx})')
if got.read_text() != PULL_DATA:
    test_fail(f'pull delivered the wrong content: {got.read_text()!r} ({ctx})')
if proc.returncode != 0:
    test_fail(f'pull delivered the content but failed ({ctx})')

proc = transfer(str(src) + '/up1', 'dummy:inbox/')
ctx = f'rc={proc.returncode}, output={proc.stdout.strip()[:300]!r}'
landed = restricted / 'inbox' / 'up1'

if proc.returncode != 0:
    test_fail(f'push through rrsync failed ({ctx})')
if not landed.is_file() or landed.read_text() != PUSH_DATA:
    test_fail(f'push did not deliver the content ({ctx})')

files = base / 'files'
files.write_text('f1\nf2\n')
proc = transfer(f'--files-from={files}', 'dummy:.', f'{dest}/')
for name in ('f1', 'f2'):
    result = dest / name
    if not result.is_file() or result.read_text() != PULL_DATA:
        test_fail(f'--files-from did not deliver {name}: {proc.stdout}')
if proc.returncode:
    test_fail(f'--files-from pull failed: {proc.stdout}')

def direct(files_from):
    return subprocess.run(
        [str(rrsync), '-ro', '-no-lock', str(restricted)],
        env={**os.environ,
             'SSH_ORIGINAL_COMMAND':
                 f'rsync --server --sender -logDtpre.iLsfxC --files-from={files_from} . .'},
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

control = direct(restricted / 'f1')
if 'invalid rsync-command syntax' in control.stdout or 'does not run rsync' in control.stdout:
    test_fail(f'files-from control did not reach path validation: {control.stdout}')
blocked = direct(outside / 'list')
if blocked.returncode == 0:
    test_fail('out-of-tree --files-from path was accepted')
if 'invalid rsync-command syntax' in blocked.stdout or 'does not run rsync' in blocked.stdout:
    test_fail(f'out-of-tree --files-from path was rejected for the wrong reason: {blocked.stdout}')

print('rrsync pull, push and files-from handling verified')

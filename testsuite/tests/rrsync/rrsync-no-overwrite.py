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
    flags = env.get('RRSYNC_FLAGS', '-wo -no-overwrite -no-lock').split()
    wrapper, root = env['RRSYNC_WRAPPER'], env['RRSYNC_ROOT']
    os.execve(wrapper, [wrapper, *flags, root], env)

from harness.rsync import (
    RSYNC, SCRATCHDIR, makepath, patched_rrsync, rmtree, rsync_argv, rsync_path_arg, test_fail,
)

base = SCRATCHDIR / 'rrsync-no-overwrite'
rmtree(base)
base.mkdir()
shim = base / 'rsync-shim'
shim.write_text('#!/bin/sh\nexec ' + rsync_path_arg(RSYNC) + ' "$@"\n')
shim.chmod(0o755)
wrapper = patched_rrsync(base, rsync_path=str(shim))
rsh = f'{shlex.quote(sys.executable)} {shlex.quote(os.path.abspath(__file__))} --shell'

def invoke(root, args, no_overwrite=True):
    flags = '-wo -no-overwrite -no-lock' if no_overwrite else '-wo -no-lock'
    env = {**os.environ, 'RRSYNC_WRAPPER': str(wrapper), 'RRSYNC_ROOT': str(root),
           'RRSYNC_FLAGS': flags}
    return subprocess.run(rsync_argv(*args), env=env, capture_output=True, text=True, timeout=20)

def refused(result, option):
    if result.returncode == 0 or f'option {option} has been disabled' not in result.stderr:
        test_fail(f'{option} was accepted: rc={result.returncode}, stderr={result.stderr!r}')

source = base / 'source'
makepath(source)
(source / 'new').write_bytes(b'NEW')
(source / 'victim').write_bytes(b'NEW')

log_root = base / 'log'
makepath(log_root)
(log_root / 'protected').write_bytes(b'POLICY\n')
log_args = ('-rI', '-M--log-file=protected', '-M--log-format=X', '-e', rsh,
            f'{source}/new', 'ignored:')
result = invoke(log_root, log_args)
refused(result, '--log-file')
if (log_root / 'protected').read_bytes() != b'POLICY\n':
    test_fail('--log-file changed a protected file')
plain_log = base / 'plain-log'
makepath(plain_log)
(plain_log / 'protected').write_bytes(b'POLICY\n')
if invoke(plain_log, log_args, False).returncode:
    test_fail('--log-file was rejected without -no-overwrite')

partial_root = base / 'partial'
makepath(partial_root / 'protected')
(partial_root / 'protected' / 'victim').write_bytes(b'POLICY')
partial_args = ('-rI', '--partial', '--partial-dir=protected', '-e', rsh,
                f'{source}/victim', 'ignored:')
result = invoke(partial_root, partial_args)
refused(result, '--partial-dir')
if (partial_root / 'protected' / 'victim').read_bytes() != b'POLICY':
    test_fail('--partial-dir consumed a protected partial file')
plain_partial = base / 'plain-partial'
makepath(plain_partial / 'protected')
(plain_partial / 'protected' / 'victim').write_bytes(b'POLICY')
if invoke(plain_partial, partial_args, False).returncode:
    test_fail('--partial-dir was rejected without -no-overwrite')

delay_root = base / 'delay'
makepath(delay_root / '.~tmp~')
(delay_root / '.~tmp~' / 'victim').write_bytes(b'POLICY')
delay_args = ('-rI', '--delay-updates', '-e', rsh, f'{source}/victim', 'ignored:')
result = invoke(delay_root, delay_args)
refused(result, '--delay-updates')
if (delay_root / '.~tmp~' / 'victim').read_bytes() != b'POLICY':
    test_fail('--delay-updates consumed a protected partial file')
plain_delay = base / 'plain-delay'
makepath(plain_delay / '.~tmp~')
(plain_delay / '.~tmp~' / 'victim').write_bytes(b'POLICY')
if invoke(plain_delay, delay_args, False).returncode:
    test_fail('--delay-updates was rejected without -no-overwrite')

backup_source = base / 'backup-source'
makepath(backup_source)
(backup_source / 'victim.bak').write_bytes(b'SOURCE')
backup_args = ('-r', '--delete', '--backup', '--suffix=.bak', '-e', rsh,
               f'{backup_source}/', 'ignored:')

def seed_backup(root):
    makepath(root)
    (root / 'victim').write_bytes(b'OLD-LIVE')
    (root / 'victim.bak').write_bytes(b'POLICY')

backup_root = base / 'backup'
seed_backup(backup_root)
result = invoke(backup_root, backup_args)
refused(result, '-b')
if (backup_root / 'victim.bak').read_bytes() != b'POLICY':
    test_fail('--backup changed a protected backup file')
plain_backup = base / 'plain-backup'
seed_backup(plain_backup)
plain_args = ('-r', '--delete', '--backup', '--suffix=.bak', '--ignore-existing',
              '-e', rsh, f'{backup_source}/', 'ignored:')
if invoke(plain_backup, plain_args, False).returncode:
    test_fail('--backup was rejected without -no-overwrite')
if (plain_backup / 'victim.bak').read_bytes() != b'OLD-LIVE':
    test_fail('backup control did not reproduce the collision')

safe_root = base / 'safe'
makepath(safe_root)
safe = invoke(safe_root, ('-rI', '-e', rsh, f'{source}/new', 'ignored:'))
if safe.returncode or (safe_root / 'new').read_bytes() != b'NEW':
    test_fail('ordinary -no-overwrite upload failed')

#!/usr/bin/env python3

import os
import shlex
import shutil
import signal
import stat
import subprocess
import sys

signal.signal(signal.SIGUSR1, signal.SIG_IGN)
signal.signal(signal.SIGUSR2, signal.SIG_IGN)
if '--shell' in sys.argv:
    i = sys.argv.index('--shell') + 2
    env = {**os.environ, 'SSH_ORIGINAL_COMMAND': ' '.join(sys.argv[i:])}
    signal.signal(signal.SIGUSR1, signal.SIG_DFL)
    signal.signal(signal.SIGUSR2, signal.SIG_DFL)
    flags = shlex.split(env['RRSYNC_FLAGS'])
    os.execve(env['RRSYNC_WRAPPER'], [env['RRSYNC_WRAPPER'], *flags, env['RRSYNC_ROOT']], env)

from rsyncfns import RSYNC, SCRATCHDIR, makepath, patched_rrsync, rsync_argv, rsync_path_arg, test_fail

base = SCRATCHDIR / 'rrsync-log'
src, root = base / 'src', base / 'root'
makepath(src / 'sub', root)
(src / 'a.txt').write_bytes(b'AAA')
(src / 'sub' / 'b.txt').write_bytes(b'BB')
log = base / 'transfer.log'
shim = base / 'rsync-shim'
shim.write_text('#!/bin/sh\nexec ' + rsync_path_arg(RSYNC) + ' "$@"\n')
shim.chmod(0o755)
wrapper = patched_rrsync(base, rsync_path=str(shim))
rsh = f'{shlex.quote(sys.executable)} {shlex.quote(os.path.abspath(__file__))} --shell'

def transfer(*options, flags=('-wo', '-no-lock'), logfile=log, sender=f'{src}/', receiver='ignored:', script=wrapper):
    flags = [*flags] + (['-log', str(logfile)] if logfile is not None else [])
    env = {**os.environ, 'RRSYNC_WRAPPER': str(script), 'RRSYNC_ROOT': str(root), 'RRSYNC_FLAGS': shlex.join(flags)}
    return subprocess.run(rsync_argv('-rI', *options, '-e', rsh, sender, receiver),
                          cwd=base, env=env, capture_output=True, text=True, timeout=20)

def check(result, error=None):
    if error is None:
        if result.returncode != 0:
            test_fail(f'transfer failed: {result.returncode}: {result.stderr}')
    elif result.returncode == 0 or error not in result.stderr:
        test_fail(f'expected {error!r}: {result.returncode}: {result.stderr}')

def recorded(path, name, marker):
    lines = path.read_text().splitlines()
    if not any(marker in line and line.endswith(' ' + name) for line in lines):
        test_fail(f'{name} missing from {path}: {lines!r}')
    if not any('sent ' in line and ' received ' in line and ' total size ' in line for line in lines):
        test_fail(f'transfer summary missing: {lines!r}')

log.write_text('previous transfer\n')
log.chmod(0o600)
check(transfer(flags=('-wo',)))
if (root / 'sub' / 'b.txt').read_bytes() != b'BB':
    test_fail('upload contents differ')
recorded(log, 'a.txt', '>f')
recorded(log, 'sub/b.txt', '>f')
downloaded = base / 'download'
check(transfer(flags=('-ro',), sender='ignored:', receiver=f'{downloaded}/'))
if (downloaded / 'sub' / 'b.txt').read_bytes() != b'BB':
    test_fail('download contents differ')
recorded(log, 'a.txt', '<f')
(root / 'obsolete').write_text('delete')
check(transfer('--delete'))
if (root / 'obsolete').exists():
    test_fail('obsolete file was not deleted')
recorded(log, 'obsolete', '*deleting')
if not log.read_text().startswith('previous transfer\n') or stat.S_IMODE(log.stat().st_mode) != 0o600:
    test_fail('logging replaced existing records or changed file permissions')
log.unlink()

for i, options in enumerate([('-M--log-format=X',), ('-q',), ('-M--info=ALL0',)]):
    selected_log = base / f'format-{i}.log'
    check(transfer(*options, logfile=selected_log))
    recorded(selected_log, 'a.txt', '>f')
selected_log = base / 'log with spaces'
check(transfer(logfile=selected_log))
recorded(selected_log, 'a.txt', '>f')
file_list = base / 'files-from'
file_list.write_text('a.txt\n')
selected_log = base / 'files-from.log'
check(transfer(f'--files-from={file_list}', flags=('-ro',), logfile=selected_log,
               sender='ignored:', receiver=f'{base}/listed/'))
recorded(selected_log, 'a.txt', '<f')
check(transfer('-M--log-file-format='), 'invalid rsync-command syntax')
check(transfer('-M--log-file=mine.log'), 'option --log-file has been disabled')
result = subprocess.run([str(wrapper), '-no-lock', '-log', str(log), str(root)],
                        env={**os.environ, 'SSH_ORIGINAL_COMMAND': 'rsync --server --log-file mine.log -r . .'},
                        stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=20)
check(result, 'option --log-file has been disabled')
if (root / 'mine.log').exists():
    test_fail('client-selected log was created')
check(transfer('-M--log-file=mine.log', logfile=None))
if not (root / 'mine.log').is_file():
    test_fail('client logging without -log stopped working')

(root / 'a.txt').write_text('keep this')
(src / 'new.txt').write_text('new')
check(transfer(flags=('-wo', '-no-overwrite')))
if (root / 'a.txt').read_text() != 'keep this' or (root / 'new.txt').read_text() != 'new':
    test_fail('-no-overwrite with administrator logging failed')
recorded(log, 'new.txt', '>f')

makepath(base / 'real' / 'inner')
os.symlink(base / 'real' / 'inner', base / 'alias')
check(transfer(logfile='alias/../relative.log'))
if not (base / 'real' / 'relative.log').is_file() or (base / 'relative.log').exists():
    test_fail('symlink followed by .. resolved to the wrong log')
recorded(base / 'real' / 'relative.log', 'a.txt', '>f')
check(transfer(logfile=base / 'alias' / 'absolute.log'))
if not (base / 'real' / 'inner' / 'absolute.log').is_file():
    test_fail('external symlink log path failed')
recorded(base / 'real' / 'inner' / 'absolute.log', 'a.txt', '>f')
leaf = base / 'leaf'
leaf.symlink_to(base / 'real' / 'leaf.log')
check(transfer(logfile=leaf))
recorded(base / 'real' / 'leaf.log', 'a.txt', '>f')
double_slash = base / 'double-slash.log'
check(transfer(logfile='/' + str(double_slash)))
recorded(double_slash, 'a.txt', '>f')

os.symlink(root, base / 'into')
os.symlink(base / 'stolen', root / 'out')
os.symlink(root / 'out', base / 'through')
for path in (root / 'transfer.log', base / 'into' / 'transfer.log', base / 'through', root / '..' / 'escaped.log'):
    check(transfer(logfile=path), 'must be outside the restricted directory')
if (root / 'transfer.log').exists() or (base / 'stolen').exists() or (base / 'escaped.log').exists():
    test_fail('a rejected log was created')

if os.geteuid() == 0:
    real = base / 'foreign-real'
    makepath(real)
    foreign_dir = base / 'foreign-dir'
    foreign_dir.symlink_to(real)
    foreign_leaf = base / 'foreign-leaf'
    foreign_leaf.symlink_to(real / 'leaf.log')
    for link, path, target in ((foreign_leaf, foreign_leaf, real / 'leaf.log'),
                               (foreign_dir, foreign_dir / 'parent.log', real / 'parent.log')):
        os.lchown(link, 65534, 65534)
        target.write_text('unchanged\n')
        result = subprocess.run(rsync_argv('-rI', f'--log-file={path}', f'{src}/', f'{root}/'),
                                capture_output=True, text=True, timeout=20)
        if ('refusing to follow a symlink owned by an untrusted user' not in result.stderr
                or target.read_text() != 'unchanged\n'):
            test_fail('ordinary --log-file ownership control failed')
        result = transfer(logfile=path)
        if target.read_text() != 'unchanged\n':
            test_fail('untrusted -log symlink changed its target')
        check(result, 'untrusted symlink in -log FILE')

regular = base / 'regular'
regular.write_text('not a directory')
os.symlink('regular/../bad.log', base / 'bad-link')
os.symlink('regular/', base / 'bad-slash')
for path in ('regular/../bad.log', 'regular/./bad.log', str(regular) + '/', base / 'bad-link', base / 'bad-slash'):
    check(transfer(logfile=path), 'cannot resolve the -log FILE')
if (base / 'bad.log').exists():
    test_fail('an invalid log path was normalised and opened')
os.symlink('loop', base / 'loop')
check(transfer(logfile=base / 'loop'), 'too many symlinks')
check(transfer(logfile=''), 'empty -log FILE')
check(transfer(logfile=base / 'real'), 'cannot open the -log FILE')
check(transfer(logfile='missing/../bad.log'), 'cannot resolve the -log FILE')
if os.geteuid() != 0:
    denied = base / 'no-search'
    denied.mkdir(mode=0o600)
    try:
        check(transfer(logfile='no-search/../bad.log'), 'cannot resolve the -log FILE')
    finally:
        denied.chmod(0o700)
    readonly = base / 'readonly.log'
    readonly.touch(mode=0o400)
    check(transfer(logfile=readonly), 'cannot open the -log FILE')

(root / 'sub' / 'b.txt').unlink()
check(transfer(logfile=base / 'missing-dir' / 'transfer.log'), 'cannot resolve the -log FILE')
if (root / 'sub' / 'b.txt').exists():
    test_fail('transfer ran despite a failed initial log open')

if hasattr(os, 'mkfifo'):
    fifo = base / 'fifo'
    os.mkfifo(fifo)
    fd = os.open(fifo, os.O_RDONLY | os.O_NONBLOCK)
    try:
        check(transfer(logfile=fifo), '-log FILE must be a regular file')
    finally:
        os.close(fd)

for command in ('true', 'not-rsync'):
    unused = base / 'unused.log'
    result = subprocess.run([str(wrapper), '-no-lock', '-log', str(unused), str(root)],
                            env={**os.environ, 'SSH_ORIGINAL_COMMAND': command},
                            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=20)
    if result.returncode != (0 if command == 'true' else 1) or unused.exists():
        test_fail(f'non-transfer command opened the log: {result.stderr}')

result = subprocess.run([str(wrapper), '-no-lock', '-log', str(log), '/'],
                        env={**os.environ, 'SSH_ORIGINAL_COMMAND': 'rsync --server -r . .'},
                        stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=20)
check(result, '-log cannot be used when DIR is /')

for fault in ('directory', 'full', 'foreign'):
    if fault == 'full' and not os.path.exists('/dev/full'):
        continue
    if fault == 'foreign' and os.geteuid() != 0:
        continue
    directory = base / fault
    makepath(directory)
    fault_log = directory / 'transfer.log'
    fault_shim = directory / 'rsync-shim'
    quoted = shlex.quote(str(fault_log))
    change = f'mkdir {quoted}' if fault == 'directory' else f'ln -s /dev/full {quoted}'
    if fault == 'foreign':
        victim = directory / 'victim'
        victim.write_text('unchanged\n')
        change = f'ln -s {shlex.quote(str(victim))} {quoted}\nchown -h 65534 {quoted}'
    fault_shim.write_text(f'#!/bin/sh\nset -e\nmv {quoted} {quoted}.old\n{change}\nexec '
                          + rsync_path_arg(RSYNC) + ' "$@"\n')
    fault_shim.chmod(0o755)
    fault_wrapper = patched_rrsync(directory, rsync_path=str(fault_shim))
    (src / f'after-{fault}').write_text('transferred')
    result = transfer(script=fault_wrapper, logfile=fault_log)
    check(result)
    if (root / f'after-{fault}').read_text() != 'transferred':
        test_fail('best-effort logging stopped the transfer')
    if fault == 'directory' and 'failed to open log-file' not in result.stderr:
        test_fail(f'log reopen failure was not reported: {result.stderr}')
    if (fault == 'foreign' and (victim.read_text() != 'unchanged\n'
            or 'refusing to follow a symlink owned by an untrusted user' not in result.stderr)):
        test_fail('the log reopen bypassed symlink ownership checks')

unshare = shutil.which('unshare')
userns = [unshare, '--user', '--map-root-user', '--mount'] if unshare else None
if userns and subprocess.run(userns + ['true'], capture_output=True, timeout=10).returncode == 0:
    bind = base / 'bind'
    makepath(bind)
    command = 'mount --bind "$1" "$2" && exec "$3" -wo -no-lock -log "$2/transfer.log" "$1"'
    result = subprocess.run(userns + ['sh', '-c', command, 'sh', str(root), str(bind), str(wrapper)],
                            env={**os.environ, 'SSH_ORIGINAL_COMMAND': 'rsync --server -r . .'},
                            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=20)
    check(result, 'must be outside the restricted directory')

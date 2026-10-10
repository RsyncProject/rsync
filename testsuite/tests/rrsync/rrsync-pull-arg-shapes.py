#!/usr/bin/env python3

import os
import shlex
import stat
import subprocess

from harness.rsync import (
    RSYNC, SCRATCHDIR, forced_protocol, makepath, patched_rrsync, rmtree,
    rsync_argv, test_fail, rsync_path_arg,
)

base = SCRATCHDIR / 'rrsync-arg-shapes'
rmtree(base)
restricted = base / 'restricted'
dest = base / 'dest'
makepath(restricted / 'sub' / 'deep', dest)

(restricted / 'f1').write_text('TOP\n')
(restricted / 'sub' / 'deep' / 'f2').write_text('DEEP\n')
os.symlink('sub/deep', restricted / 'alias')
os.mkfifo(restricted / 'afifo')
os.symlink('missing-sibling', restricted / 'dangling')
os.symlink('f1', restricted / 'goodlink')

xonly = restricted / 'xonly'
xonly.mkdir()
(xonly / 'f3').write_text('XONLY\n')
xonly.chmod(0o111)

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

def pull(*args):
    rmtree(dest)
    dest.mkdir()
    proc = subprocess.run(rsync_argv('-a', '-e', str(rsh), *args,
                                     str(dest) + '/'),
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          text=True)
    got = sorted(str(p.relative_to(dest)) for p in dest.rglob('*'))
    return proc, got

KINDS = {
    'f1':          ('reg', 'TOP\n'),
    'f2':          ('reg', 'DEEP\n'),
    'f3':          ('reg', 'XONLY\n'),
    'deep':        ('dir', None),
    'deep/f2':     ('reg', 'DEEP\n'),
    'sub':         ('dir', None),
    'sub/deep':    ('dir', None),
    'sub/deep/f2': ('reg', 'DEEP\n'),
    'dangling':    ('sym', 'missing-sibling'),
    'goodlink':    ('sym', 'f1'),
}

def describe(path):
    if path.is_symlink():
        return 'a symlink to %r' % os.readlink(path)
    if path.is_dir():
        return 'a directory'
    if path.is_file():
        return 'a regular file'
    return 'neither a file, a directory nor a symlink'

def check_kinds(label, names, ctx):
    for name in names:
        want = KINDS.get(name)
        if want is None:
            test_fail(f'{label}: delivered {name!r}, which has no entry in '
                      'KINDS, so nothing checked what it is -- add one')
        kind, detail = want
        path = dest / name
        if kind == 'sym':
            if not path.is_symlink():
                test_fail(f'{label}: {name} should be a symlink, got '
                          f'{describe(path)} ({ctx})')
            if os.readlink(path) != detail:
                test_fail(f'{label}: {name} points at {os.readlink(path)!r}, '
                          f'expected {detail!r} ({ctx})')
        elif kind == 'dir':
            if path.is_symlink() or not path.is_dir():
                test_fail(f'{label}: {name} should be a directory, got '
                          f'{describe(path)} ({ctx})')
        elif kind != 'reg':
            test_fail(f'{label}: {name} has unknown kind {kind!r} in KINDS')
        else:
            if path.is_symlink() or not path.is_file():
                test_fail(f'{label}: {name} should be a regular file, got '
                          f'{describe(path)} ({ctx})')
            if path.read_text() != detail:
                test_fail(f'{label}: {name} holds {path.read_text()!r}, '
                          f'expected {detail!r} ({ctx})')

CASES = [
    ('file',          ['dummy:f1'],                       ['f1']),
    ('deep file',     ['dummy:sub/deep/f2'],              ['f2']),
    ('dir',           ['dummy:sub'],                      ['sub', 'sub/deep', 'sub/deep/f2']),
    ('dir + slash',   ['dummy:sub/'],                     ['deep', 'deep/f2']),
    ('dir + /.',      ['dummy:sub/.'],                    ['deep', 'deep/f2']),
    ('-R deep file',  ['-R', 'dummy:sub/deep/f2'],        ['sub', 'sub/deep', 'sub/deep/f2']),
    ('-R dir + slash',['-R', 'dummy:sub/'],               ['sub', 'sub/deep', 'sub/deep/f2']),
    ('-R client /./', ['-R', 'dummy:sub/./deep/f2'],      ['deep', 'deep/f2']),
    ('-R terminal /./',   ['-R', 'dummy:sub/./'],          ['deep', 'deep/f2']),
    ('-R terminal /./.',  ['-R', 'dummy:sub/./.'],         ['deep', 'deep/f2']),
    ('symlinked parent',  ['dummy:alias/f2'],              ['f2']),
    ('-R symlinked parent', ['-R', 'dummy:alias/./f2'],    ['f2']),
]

if forced_protocol() is None or forced_protocol() >= 30:
    CASES.append(
    ('-R no-implied', ['-R', '--no-implied-dirs', 'dummy:sub/deep/f2'],
                                                          ['sub', 'sub/deep', 'sub/deep/f2']))

CASES += [
    ('search-only parent', ['dummy:xonly/f3'],            ['f3']),
    ('dangling symlink',   ['dummy:dangling'],            ['dangling']),
    ('symlink to a file',  ['dummy:goodlink'],            ['goodlink']),
    ('two args',      ['dummy:f1', 'dummy:sub/deep/f2'],  ['f1', 'f2']),
]

for label, argv, expect in CASES:
    proc, got = pull(*argv)
    ctx = f'rc={proc.returncode}, output={proc.stdout.strip()[:200]!r}'
    if got != expect:
        test_fail(f'{label}: delivered {got}, expected {expect} ({ctx})')
    check_kinds(label, got, ctx)
    if proc.returncode != 0:
        test_fail(f'{label}: delivered the right tree but failed ({ctx})')

rmtree(dest)
dest.mkdir()
try:
    proc = subprocess.run(rsync_argv('-a', '-e', str(rsh), 'dummy:afifo',
                                     str(dest) + '/'),
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          text=True, timeout=60)
except subprocess.TimeoutExpired:
    test_fail('pulling a FIFO hung: either the wrapper opened it and blocked '
              'for a writer, or the file list desynchronised and both ends '
              'stalled')
ctx = f'rc={proc.returncode}, output={proc.stdout.strip()[:200]!r}'
if proc.returncode != 0:
    test_fail(f'pulling a FIFO failed ({ctx})')
if not stat.S_ISFIFO(os.lstat(dest / 'afifo').st_mode):
    test_fail(f'pulling a FIFO delivered {describe(dest / "afifo")} ({ctx})')

xonly.chmod(0o755)

print(f'rrsync delivers all {len(CASES)} pull argument shapes')

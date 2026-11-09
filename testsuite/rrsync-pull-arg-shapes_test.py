#!/usr/bin/env python3
"""Every pull argument shape through rrsync must deliver what it names.

rrsync inode-pins each validated argument so the exec'd rsync cannot be
race-flipped between realpath() and resolution.  Which pin is usable depends on
what rsync does with the argument, and getting that wrong is silent: the
transfer "succeeds" while delivering nothing, or delivers the file under the
wrong name.

That is exactly what happened when the pin was first added -- of the shapes
below only "dir-slash" survived, because it is the one rsync opens rather than
lstat()s.  Nothing covered --relative at all, so its breakage went unnoticed
too.

Each case asserts the delivered tree, which is the user-visible contract and is
independent of which pin the implementation chooses.
"""

import os
import shlex
import stat
import subprocess

from rsyncfns import (
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
# An in-tree directory symlink, the ordinary way to publish a path under
# another name. Reaching through it must keep working.
os.symlink('sub/deep', restricted / 'alias')
# A file reachable only by name: search permission on the parent, no read.
# rrsync must not need to list a directory just to pin it.
# A FIFO and a dangling symlink: rsync only needs to describe these, never to
# open them.  Opening a FIFO blocks for a writer, and resolving a dangling link
# reaches nothing -- both wedged or failed the pull until the pin learned to
# leave them alone.
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


# What every deliverable name must BE.  Comparing names alone is not enough:
# an empty directory called "f1", or a correctly-named symlink carrying no
# data, satisfies a name-only expectation -- and a symlink with no data is
# exactly what handing the sender a procfs magic link produced.
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
            # Closed schema: a mistyped token must not fall through to the
            # regular-file arm and quietly assert the wrong thing.
            test_fail(f'{label}: {name} has unknown kind {kind!r} in KINDS')
        else:
            if path.is_symlink() or not path.is_file():
                test_fail(f'{label}: {name} should be a regular file, got '
                          f'{describe(path)} ({ctx})')
            if path.read_text() != detail:
                test_fail(f'{label}: {name} holds {path.read_text()!r}, '
                          f'expected {detail!r} ({ctx})')


# Expectations are what a pristine 3.4.4 rrsync delivers for the same command.
CASES = [
    ('file',          ['dummy:f1'],                       ['f1']),
    ('deep file',     ['dummy:sub/deep/f2'],              ['f2']),
    ('dir',           ['dummy:sub'],                      ['sub', 'sub/deep', 'sub/deep/f2']),
    ('dir + slash',   ['dummy:sub/'],                     ['deep', 'deep/f2']),
    ('dir + /.',      ['dummy:sub/.'],                    ['deep', 'deep/f2']),
    ('-R deep file',  ['-R', 'dummy:sub/deep/f2'],        ['sub', 'sub/deep', 'sub/deep/f2']),
    ('-R dir + slash',['-R', 'dummy:sub/'],               ['sub', 'sub/deep', 'sub/deep/f2']),
    ('-R client /./', ['-R', 'dummy:sub/./deep/f2'],      ['deep', 'deep/f2']),
    # A marker with nothing after it means "start the transmitted name here",
    # so the argument's own directory name must NOT appear in the result.
    ('-R terminal /./',   ['-R', 'dummy:sub/./'],          ['deep', 'deep/f2']),
    ('-R terminal /./.',  ['-R', 'dummy:sub/./.'],         ['deep', 'deep/f2']),
    # An in-tree directory symlink as the argument's parent is ordinary and
    # must keep working; pinning it must not refuse to follow it.
    ('symlinked parent',  ['dummy:alias/f2'],              ['f2']),
    ('-R symlinked parent', ['-R', 'dummy:alias/./f2'],    ['f2']),
]

# At protocol 29 the receiver rejects "-R --no-implied-dirs" with "invalid path
# from sender" and transfers nothing.  That is rsync's own behaviour, not the
# wrapper's -- a pristine 3.4.4 rrsync fails identically -- so only assert the
# case where it means something.
if forced_protocol() is None or forced_protocol() >= 30:
    CASES.append(
    ('-R no-implied', ['-R', '--no-implied-dirs', 'dummy:sub/deep/f2'],
                                                          ['sub', 'sub/deep', 'sub/deep/f2']))

CASES += [
    ('search-only parent', ['dummy:xonly/f3'],            ['f3']),
    # These three are what a pristine 3.4.4 rrsync delivers for the same args.
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

# A FIFO must not WEDGE the wrapper.  The pin used to open every validated path
# O_RDONLY, and opening a FIFO blocks until a writer appears, so naming one hung
# rrsync before exec and an authorised user could pile up stuck processes.
#
# It must also arrive.  rrsync denies device/special CREATION on the receiving
# side only, so a pull carries the client's own -D and delivers the FIFO, which
# is what a pristine 3.4.4 rrsync does.  While --no-D was forced here too, the
# file list desynchronised -- the sender omitted the rdev fields the client's
# receiver still read -- and this could assert nothing beyond "did not hang":
# it overflowed at protocol 29/30 and, as root, deadlocked instead.
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

# The kinds, the symlink targets and the file contents are asserted by
# check_kinds() on every case above, rather than spot-checked on a few here.

xonly.chmod(0o755)

print(f'rrsync delivers all {len(CASES)} pull argument shapes')

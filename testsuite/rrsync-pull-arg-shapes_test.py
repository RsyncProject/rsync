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
import subprocess

from rsyncfns import (
    RSYNC, SCRATCHDIR, forced_protocol, makepath, patched_rrsync, rmtree,
    rsync_argv, test_fail,
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
xonly = restricted / 'xonly'
xonly.mkdir()
(xonly / 'f3').write_text('XONLY\n')
xonly.chmod(0o111)

shim = base / 'rsync-shim'
shim.write_text('#!/bin/sh\nexec ' + RSYNC + ' "$@"\n')
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
    ('two args',      ['dummy:f1', 'dummy:sub/deep/f2'],  ['f1', 'f2']),
]

for label, argv, expect in CASES:
    proc, got = pull(*argv)
    ctx = f'rc={proc.returncode}, output={proc.stdout.strip()[:200]!r}'
    if got != expect:
        test_fail(f'{label}: delivered {got}, expected {expect} ({ctx})')
    if proc.returncode != 0:
        test_fail(f'{label}: delivered the right tree but failed ({ctx})')

# The content has to survive too, not just the names: handing the sender a
# procfs magic link produced correctly-named *symlinks* with no data.
proc, _ = pull('dummy:sub/deep/f2')
if (dest / 'f2').is_symlink() or (dest / 'f2').read_text() != 'DEEP\n':
    test_fail('pull delivered the name but not the content')

xonly.chmod(0o755)

print(f'rrsync delivers all {len(CASES)} pull argument shapes')

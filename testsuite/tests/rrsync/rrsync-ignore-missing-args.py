#!/usr/bin/env python3

import subprocess

from fixtures.rrsync import rrsync_shell, rsync_wrapper, patched_rrsync
from harness.environment import SCRATCHDIR
from harness.filesystem import makepath, rmtree
from harness.results import test_fail
from harness.rsync import rsync_argv
from harness import metadata

metadata(transports={'pipe'})

base = SCRATCHDIR / 'rrsync-ignore-missing-args'
rmtree(base)
restricted = base / 'restricted'

makepath(restricted / 'sub')
(restricted / 'f1').write_text('F1\n')

shim = base / 'rsync-shim'
rsync_wrapper(shim)

rrsync = patched_rrsync(base, rsync_path=str(shim))

rsh = base / 'fake-rsh'
rrsync_shell(rsh, rrsync, restricted)

def transfer(name, *args, opts='-a'):
    dest = base / name
    makepath(dest)
    proc = subprocess.run(rsync_argv(opts, '--ignore-missing-args', '-e', str(rsh),
                                     *[f'dummy:{s}' for s in args], f'{dest}/'),
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    return proc, dest, f'rc={proc.returncode}, output={proc.stdout.strip()[:300]!r}'

# Missing operands are skipped only when rsync would not change into them.
for name, opts, sources, copied in [
        ['leaf', '-a', ['f1', 'missing', 'sub/missing'], ['f1']],
        ['leaf-relative', '-aR', ['f1', 'sub/missing', 'nodir/missing', 'sub/./nodir/missing'], ['f1']],
        ['dir-relative-slash', '-aR', ['f1', 'nodir/'], ['f1']],
        ['dir-relative-dot', '-aR', ['f1', 'nodir/.'], ['f1']],
        ['dir-relative-nested', '-aR', ['f1', 'nodir/child/'], ['f1']],
        ['dir-relative-anchor', '-aR', ['f1', 'sub/./nodir/'], ['f1']]]:
    proc, dest, ctx = transfer(name, *sources, opts=opts)
    if proc.returncode != 0:
        test_fail(f'{name}: pull with a missing operand failed ({ctx})')
    if sorted(p.name for p in dest.iterdir()) != copied:
        test_fail(f'{name}: pull with a missing operand copied {sorted(dest.iterdir())} ({ctx})')
    if not (dest / 'f1').is_file() or (dest / 'f1').read_text() != 'F1\n':
        test_fail(f'{name}: remaining source content was not transferred ({ctx})')

# A missing dir that rsync would chdir to stays an error, as in stock rsync.
for name, opts, source in [
        ['parent', '-a', 'nodir/missing'],
        ['parent-relative', '-aR', 'nodir/./missing'],
        ['dir-plain-slash', '-a', 'nodir/'],
        ['dir-plain-dot', '-a', 'nodir/.'],
        ['dir-relative-missing-anchor', '-aR', 'nodir/./']]:
    proc, dest, ctx = transfer(name, 'f1', source, opts=opts)
    if proc.returncode == 0:
        test_fail(f'{name}: pull with a missing parent dir succeeded ({ctx})')
    if source not in proc.stdout:
        test_fail(f'{name}: pull with a missing parent dir failed for the wrong reason ({ctx})')
    if any(dest.iterdir()):
        test_fail(f'{name}: rejected transfer copied data ({ctx})')

# An existing operand that fails while being pinned is not a missing argument.
(restricted / 'dangling').symlink_to('missing-target')
proc, dest, ctx = transfer('pin-failure', 'f1', 'dangling/', opts='-aR')
if proc.returncode == 0 or 'post-realpath open failed' not in proc.stdout:
    test_fail(f'pinning failure was mistaken for a missing argument ({ctx})')
if any(dest.iterdir()):
    test_fail(f'pinning failure copied data ({ctx})')

# All sources missing: rrsync refuses rather than letting rsync send ".".
proc, dest, ctx = transfer('all-missing', 'missing', 'sub/missing')
if proc.returncode == 0:
    test_fail(f'pull with all sources missing succeeded ({ctx})')
if 'all source args are missing' not in proc.stdout:
    test_fail(f'pull with all sources missing failed for the wrong reason ({ctx})')
if any(dest.iterdir()):
    test_fail(f'pull with all sources missing copied {sorted(dest.iterdir())} ({ctx})')

for name, source in [('all-missing-dir-slash', 'nodir/'),
                     ('all-missing-dir-dot', 'nodir/.')]:
    proc, dest, ctx = transfer(name, source, opts='-aR')
    if proc.returncode == 0 or 'all source args are missing' not in proc.stdout:
        test_fail(f'{name}: empty transfer was not refused ({ctx})')
    if any(dest.iterdir()):
        test_fail(f'{name}: empty transfer copied data ({ctx})')

print('rrsync --ignore-missing-args verified')

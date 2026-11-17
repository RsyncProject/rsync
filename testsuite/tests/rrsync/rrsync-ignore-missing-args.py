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

# A missing leaf is skipped, the rest is transferred.
for name, opts, sources, copied in [
        ['leaf', '-a', ['f1', 'missing', 'sub/missing'], ['f1']],
        ['leaf-relative', '-aR', ['f1', 'sub/missing', 'nodir/missing', 'sub/./nodir/missing'], ['f1']]]:
    proc, dest, ctx = transfer(name, *sources, opts=opts)
    if proc.returncode != 0:
        test_fail(f'{name}: pull with a missing leaf failed ({ctx})')
    if sorted(p.name for p in dest.iterdir()) != copied:
        test_fail(f'{name}: pull with a missing leaf copied {sorted(dest.iterdir())} ({ctx})')

# A missing dir that rsync would chdir to stays an error, as in stock rsync.
for name, opts, source in [
        ['parent', '-a', 'nodir/missing'],
        ['parent-relative', '-aR', 'nodir/./missing']]:
    proc, dest, ctx = transfer(name, 'f1', source, opts=opts)
    if proc.returncode == 0:
        test_fail(f'{name}: pull with a missing parent dir succeeded ({ctx})')
    if source not in proc.stdout:
        test_fail(f'{name}: pull with a missing parent dir failed for the wrong reason ({ctx})')

# All sources missing: rrsync refuses rather than letting rsync send ".".
proc, dest, ctx = transfer('all-missing', 'missing', 'sub/missing')
if proc.returncode == 0:
    test_fail(f'pull with all sources missing succeeded ({ctx})')
if 'all source args are missing' not in proc.stdout:
    test_fail(f'pull with all sources missing failed for the wrong reason ({ctx})')
if any(dest.iterdir()):
    test_fail(f'pull with all sources missing copied {sorted(dest.iterdir())} ({ctx})')

print('rrsync --ignore-missing-args verified')

#!/usr/bin/env python3
"""An in-band symlink at an alt-dest name must not read outside the restricted dir.

--link-dest/--compare-dest/--copy-dest name a basis directory rsync only reads
through.  A missing one is the ordinary first-run case, so it cannot simply be
refused -- but leaving the name for rsync to resolve lets the same transfer
plant a symlink there and have the basis lookup read out of the tree.  With
--copy-dest a matching basis file is copied into the destination, so the
out-of-tree content lands where it can be read back.
"""

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
    wrapper, root = env['RRSYNC_WRAPPER'], env['RRSYNC_ROOT']
    os.execve(wrapper, [wrapper, '-wo', root], env)

from rsyncfns import (RSYNC, SCRATCHDIR, makepath, patched_rrsync,
                      proc_self_fd_pins, rmtree, rsync_argv, rsync_path_arg,
)

T = 1234567890

base = SCRATCHDIR / 'rrsync-alt-dest-inband-pivot'
rmtree(base)
src = base / 'src'
root = base / 'restricted'
outside = base / 'outside'
makepath(src, root, outside)

# Same length and mtime as the source file, so rsync's quick check calls the
# basis a match and --copy-dest copies it instead of transferring.
(outside / 'f0').write_bytes(b'ADMIN-SECRET\n')
(src / 'f0').write_bytes(b'CLIENT-DATA0\n')
# The pivot has to be INSTALLED before the generator does f0's basis lookup,
# and the generator runs ahead of the receiver -- sorting 'basis' first is not
# enough on its own, and a run where the generator wins would pass vacuously.
# Put a few thousand files in between: the receiver has only the symlink to
# write while the generator has to work through all of them, so by the time it
# reaches f0 the symlink is long since in place.  The assertion after the run
# checks that it really was, rather than trusting the ordering.
os.symlink('../outside', src / 'basis')
filler = src / 'c'
filler.mkdir()
for i in range(3000):
    (filler / ('%04d' % i)).write_bytes(b'.')
os.utime(src / 'f0', (T, T))
os.utime(outside / 'f0', (T, T))

# RSYNC may be a multi-word command (the runner's --protocol=N, or valgrind)
# while rrsync hands its RSYNC to execlp() as a single executable name, so it
# has to be wrapped before patched_rrsync() sees it.
shim = base / 'rsync-shim'
shim.write_text('#!/bin/sh\nexec ' + rsync_path_arg(RSYNC) + ' "$@"\n')
shim.chmod(0o755)
wrapper = patched_rrsync(base, rsync_path=str(shim))
rsh = f'{shlex.quote(sys.executable)} {shlex.quote(os.path.abspath(__file__))} --shell'
env = {**os.environ, 'RRSYNC_WRAPPER': str(wrapper), 'RRSYNC_ROOT': str(root)}

got = subprocess.run(
    rsync_argv('-rlt', '--copy-dest=basis', '-e', rsh, f'{src}/', 'ignored:'),
    env=env, capture_output=True, text=True, timeout=60)

landed = (root / 'f0').read_bytes() if (root / 'f0').exists() else None
pivot = os.readlink(root / 'basis') if (root / 'basis').is_symlink() else None
print(f'rc={got.returncode}, landed={landed!r}, pivot={pivot!r}, '
      f'stderr={got.stderr!r}', flush=True)
assert landed != b'ADMIN-SECRET\n', (
    'rrsync let a --copy-dest symlink created by the same transfer read a file '
    'outside the restricted directory and deliver it to the client')

if proc_self_fd_pins():
    # rrsync stands in an empty unlinked directory: the basis lookup finds
    # nothing, so the file transfers normally and first-run alt-dest keeps
    # working.  That it SUCCEEDS is the point -- refusing would have been the
    # easy answer and would break every first-run --link-dest.
    assert pivot == '../outside', (
        'the transfer did not install the pivot symlink, so nothing was in '
        f'place for the basis lookup to follow and this run proves nothing: '
        f'pivot={pivot!r}')
    assert got.returncode == 0, (
        f'the transfer failed instead of finding an empty basis: '
        f'rc={got.returncode}, stderr={got.stderr!r}')
    assert landed == b'CLIENT-DATA0\n', (
        f'the source file did not transfer: landed={landed!r}')
else:
    # Without the /proc/self/fd primitive there is no way to hand rsync an
    # inode instead of a name, so the option is refused BEFORE rsync runs --
    # which is why there is no pivot to find here.
    assert got.returncode != 0 and 'receiver option path does not exist' in got.stderr, (
        f'a missing alt-dest was neither pinned nor refused: '
        f'rc={got.returncode}, stderr={got.stderr!r}')
    assert pivot is None and landed is None, (
        f'the refusal did not stop the transfer: pivot={pivot!r}, '
        f'landed={landed!r}')

# A basis directory that really exists must still be usable as one.
makepath(root / 'real-basis')
(root / 'real-basis' / 'f1').write_bytes(b'BASIS-MATCH\n')
(src / 'f1').write_bytes(b'BASIS-MATCH\n')
os.utime(src / 'f1', (T, T))
os.utime(root / 'real-basis' / 'f1', (T, T))
ok = subprocess.run(
    rsync_argv('-rlt', '--copy-dest=real-basis', '-e', rsh,
               f'{src}/f1', 'ignored:'),
    env=env, capture_output=True, text=True, timeout=20)
assert ok.returncode == 0 and (root / 'f1').read_bytes() == b'BASIS-MATCH\n', (
    f'an existing --copy-dest basis stopped working: rc={ok.returncode}, '
    f'stderr={ok.stderr!r}')

# --compare-dest takes the same route and must be covered too: a match there
# makes rsync skip the file entirely rather than copy it, so the tell is the
# file going MISSING from the destination.
rmtree(root)
makepath(root)
got = subprocess.run(
    rsync_argv('-rlt', '--compare-dest=basis', '-e', rsh, f'{src}/', 'ignored:'),
    env=env, capture_output=True, text=True, timeout=60)
landed = (root / 'f0').read_bytes() if (root / 'f0').exists() else None
pivot = os.readlink(root / 'basis') if (root / 'basis').is_symlink() else None
if proc_self_fd_pins():
    assert pivot == '../outside' and got.returncode == 0, (
        'the --compare-dest run did not install the pivot or did not complete, '
        f'so it proves nothing: pivot={pivot!r}, rc={got.returncode}, '
        f'stderr={got.stderr!r}')
    assert landed == b'CLIENT-DATA0\n', (
        'the out-of-tree basis was consulted through the --compare-dest '
        f'symlink, so rsync skipped transferring the file: landed={landed!r}, '
        f'stderr={got.stderr!r}')
else:
    assert got.returncode != 0 and 'receiver option path does not exist' in got.stderr, (
        f'a missing --compare-dest was neither pinned nor refused: '
        f'rc={got.returncode}, stderr={got.stderr!r}')
    assert pivot is None, f'the refusal did not stop the transfer: pivot={pivot!r}'

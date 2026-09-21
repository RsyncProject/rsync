#!/usr/bin/env python3
"""rrsync must not let one transfer create and consume an option-path symlink."""

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

from rsyncfns import (RSYNC, SCRATCHDIR, makepath, patched_rrsync,
                      proc_self_fd_pins, rmtree, rsync_argv, rsync_path_arg,
)

# Handing rsync an inode instead of a name needs the /proc/self/fd primitive.
# Where it is missing there is no safe way to pass a peer-suppliable option
# path, so rrsync refuses it outright -- the escape is blocked either way, but
# only the pinning answer also keeps first use working.
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

# RSYNC may be a multi-word command (the runner's --protocol=N, or valgrind)
# while rrsync hands its RSYNC to execlp() as a single executable name, so it
# has to be wrapped before patched_rrsync() sees it.
shim = base / 'rsync-shim'
shim.write_text('#!/bin/sh\nexec ' + rsync_path_arg(RSYNC) + ' "$@"\n')
shim.chmod(0o755)
wrapper = patched_rrsync(base, rsync_path=str(shim))
rsh = f'{shlex.quote(sys.executable)} {shlex.quote(os.path.abspath(__file__))} --shell'
env = {**os.environ, 'RRSYNC_WRAPPER': str(wrapper), 'RRSYNC_ROOT': str(root)}

seed = subprocess.run(
    rsync_argv('-rlI', '-e', rsh, f'{stage}/authorized_keys', 'ignored:'),
    env=env, capture_output=True, text=True, timeout=20)
assert seed.returncode == 0
assert (root / 'authorized_keys').read_bytes() == b'ATTACKER-SSH-KEY\n'
assert not (root / 'a').exists()

os.symlink('../outside', root / 'a')
blocked = subprocess.run(
    rsync_argv('-rlbI', '--backup-dir=a', '-e', rsh,
               f'{src}/', 'ignored:'),
    env=env, capture_output=True, text=True, timeout=20)
assert blocked.returncode != 0
assert (outside / 'authorized_keys').read_bytes() == b'ADMIN-SSH-KEY\n'
(root / 'a').unlink()

seed = subprocess.run(
    rsync_argv('-rlI', '-e', rsh, f'{stage}/authorized_keys', 'ignored:'),
    env=env, capture_output=True, text=True, timeout=20)
assert seed.returncode == 0

got = subprocess.run(
    rsync_argv('-rlbI', '--backup-dir=a', '-e', rsh,
               f'{src}/', 'ignored:'),
    env=env, capture_output=True, text=True, timeout=20)

escaped = outside / 'authorized_keys'
pivot = os.readlink(root / 'a') if (root / 'a').is_symlink() else None
print(f'seed_rc={seed.returncode}, rc={got.returncode}, pivot={pivot!r}, '
      f'escaped={escaped.read_bytes()!r}, stderr={got.stderr!r}', flush=True)
assert escaped.read_bytes() == b'ADMIN-SSH-KEY\n', (
    'rrsync followed a backup-dir symlink created by the same transfer and '
    'replaced an existing file outside its restricted directory')
# ...and it has to be blocked by THIS policy.  "the outside file is unchanged"
# is also satisfied by any unrelated failure, so require a nonzero status plus
# the evidence of the mechanism that produced it.
assert got.returncode != 0, (
    f'the pivot did not overwrite the outside file, but the transfer also did '
    f'not fail, so nothing stopped it: rc={got.returncode}, '
    f'stderr={got.stderr!r}')
if PINS:
    # rrsync created the backup dir itself before exec, so the transfer's own
    # symlink could only unlink that name, not redirect the held inode -- which
    # is why the backup fails instead of escaping.  The symlink must actually
    # have been planted, or the pivot was never attempted.
    assert pivot == '../outside', (
        f'the transfer did not plant the pivot symlink, so the run proves '
        f'nothing: pivot={pivot!r}')
    assert '/proc/self/fd/' in got.stderr, (
        'the transfer failed, but not through the pinned backup dir: an '
        f'unrelated failure would satisfy the check above: stderr={got.stderr!r}')
else:
    assert 'receiver option path does not exist' in got.stderr, (
        'the transfer failed, but not with the missing-option-path refusal: '
        f'an unrelated failure would satisfy the check above: '
        f'stderr={got.stderr!r}')

# --temp-dir: rsync does not create it, so a missing one is refused on every
# platform rather than being conjured up.
tmpdir = subprocess.run(
    rsync_argv('-rlI', '--temp-dir=no-such-tmp', '-e', rsh,
               f'{src}/authorized_keys', 'ignored:'),
    env=env, capture_output=True, text=True, timeout=20)
assert tmpdir.returncode != 0 and 'receiver option path does not exist' in tmpdir.stderr, (
    f'a missing --temp-dir was not refused: rc={tmpdir.returncode}, '
    f'stderr={tmpdir.stderr!r}')
assert not (root / 'no-such-tmp').exists(), (
    'a missing --temp-dir must not be created either')

if not PINS:
    # Without the pin primitive every peer-suppliable option path whose leaf is
    # missing is refused, so the first-use cases below cannot apply.  Check the
    # refusal itself and stop: the escape was already shown blocked above.
    for opt in ('--backup-dir=fresh-backup', '--partial-dir=.rsync-partial',
                '--link-dest=no-such-basis'):
        r = subprocess.run(
            rsync_argv('-rlbI', opt, '-e', rsh, f'{src}/authorized_keys', 'ignored:'),
            env=env, capture_output=True, text=True, timeout=20)
        assert r.returncode != 0 and 'receiver option path does not exist' in r.stderr, (
            f'{opt} was neither pinned nor refused without /proc/self/fd: '
            f'rc={r.returncode}, stderr={r.stderr!r}')
    sys.exit(0)

# Controls come after the security assertions on purpose: an environment where
# a control fails for an unrelated reason must not be able to mask the escape
# check above by aborting first.
def reseed():
    r = subprocess.run(
        rsync_argv('-rlI', '-e', rsh, f'{stage}/authorized_keys', 'ignored:'),
        env=env, capture_output=True, text=True, timeout=20)
    assert r.returncode == 0, f'reseed failed: stderr={r.stderr!r}'
    assert (root / 'authorized_keys').read_bytes() == b'ATTACKER-SSH-KEY\n'


reseed()

# An ordinary in-tree backup into a directory that already exists.
(root / 'backup').mkdir()
normal = subprocess.run(
    rsync_argv('-rlbI', '--backup-dir=backup', '-e', rsh,
               f'{src}/authorized_keys', 'ignored:'),
    env=env, capture_output=True, text=True, timeout=20)
assert normal.returncode == 0, f"ordinary in-tree backup failed: stderr={normal.stderr!r}"
assert (root / 'backup' / 'authorized_keys').read_bytes() == b'ATTACKER-SSH-KEY\n'

# A first-use --backup-dir must still WORK: rrsync creates the missing leaf
# under the pinned parent rather than refusing it, so dated/rotating backup
# directory names keep working through a restricted account.
reseed()
firstuse = subprocess.run(
    rsync_argv('-rlbI', '--backup-dir=fresh-backup', '-e', rsh,
               f'{src}/authorized_keys', 'ignored:'),
    env=env, capture_output=True, text=True, timeout=20)
assert firstuse.returncode == 0, (
    f'a first-use --backup-dir was refused: rc={firstuse.returncode}, '
    f'stderr={firstuse.stderr!r}')
fresh = root / 'fresh-backup'
assert fresh.is_dir() and not fresh.is_symlink(), (
    f'first-use --backup-dir did not leave a real directory: {fresh}')
assert (fresh / 'authorized_keys').read_bytes() == b'ATTACKER-SSH-KEY\n', (
    'the backup did not land in the created backup dir, so the run above '
    'proves nothing about --backup-dir working')

# A first-use --backup-dir may also name a whole missing hierarchy: rsync
# builds one itself (make_bak_dir()), so rrsync has to as well rather than
# stopping at "the immediate parent must exist".
reseed()
nested = subprocess.run(
    rsync_argv('-rlbI', '--backup-dir=bak/2026/07', '-e', rsh,
               f'{src}/authorized_keys', 'ignored:'),
    env=env, capture_output=True, text=True, timeout=20)
assert nested.returncode == 0, (
    f'a nested first-use --backup-dir was refused: rc={nested.returncode}, '
    f'stderr={nested.stderr!r}')
assert (root / 'bak' / '2026' / '07' / 'authorized_keys').exists(), (
    'the nested backup dir was accepted but the backup did not land in it')

# Same for a first-use --partial-dir, the resumable-upload idiom -- and it must
# be created 0700 as rsync creates it (util1.c), not 0777&~umask: a partial
# file is an incomplete copy of the peer's data and is not for the rest of the
# machine to read.
partial = subprocess.run(
    rsync_argv('-rlI', '--partial', '--partial-dir=.rsync-partial', '-e', rsh,
               f'{src}/authorized_keys', 'ignored:'),
    env=env, capture_output=True, text=True, timeout=20)
assert partial.returncode == 0, (
    f'a first-use --partial-dir was refused: rc={partial.returncode}, '
    f'stderr={partial.stderr!r}')
pdir = root / '.rsync-partial'
assert pdir.is_dir() and not pdir.is_symlink(), (
    f'first-use --partial-dir did not leave a real directory: {pdir}')
mode = pdir.stat().st_mode & 0o777
assert mode == 0o700, (
    f'the partial dir was created {mode:04o}, not 0700: partial files would be '
    'readable by other users on the server')

# A missing alt-dest is neither created nor refused: naming a basis directory
# that does not exist yet is the ordinary first-run case.  rrsync stands in an
# empty unlinked directory, so the lookup finds nothing and no name is left for
# an in-band symlink to take over (see rrsync-alt-dest-inband-pivot).
altdest = subprocess.run(
    rsync_argv('-rlI', '--link-dest=no-such-basis', '-e', rsh,
               f'{src}/authorized_keys', 'ignored:'),
    env=env, capture_output=True, text=True, timeout=20)
assert altdest.returncode == 0, (
    f'a first-run --link-dest was refused: rc={altdest.returncode}, '
    f'stderr={altdest.stderr!r}')
assert not (root / 'no-such-basis').exists(), (
    'a missing --link-dest must not be created')
assert not list(root.glob('.rrsync-empty-basis*')), (
    'the basis placeholder was left behind in the restricted dir')

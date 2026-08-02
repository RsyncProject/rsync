#!/usr/bin/env python3
"""An rrsync restricted dir must bound the merge files the peer can name.

Filter rules travel over the protocol rather than in the argv rrsync validates,
so a dir-merge rule can name a file outside the restricted dir and have the
server read it in as rules.  Redacting the resulting diagnostics is not enough:
an exclude-only merge (the "-" modifier) turns every line into a pattern, so
nothing fails to parse and the client reads the file's contents off its own
file list instead -- no error, no verbosity, and on a pull no --delete either.
"""

import os
import shlex
import signal
import subprocess
import sys

signal.signal(signal.SIGUSR1, signal.SIG_IGN)
signal.signal(signal.SIGUSR2, signal.SIG_IGN)
if '--shell' in sys.argv:
    i = sys.argv.index('--shell') + 2
    env = {**os.environ, 'SSH_ORIGINAL_COMMAND': ' '.join(sys.argv[i:])}
    signal.signal(signal.SIGUSR1, signal.SIG_DFL)
    signal.signal(signal.SIGUSR2, signal.SIG_DFL)
    wrapper, root = env['RRSYNC_WRAPPER'], env['RRSYNC_ROOT']
    os.execve(wrapper, [wrapper, '-ro', '-no-lock', root], env)

from rsyncfns import RSYNC, SCRATCHDIR, makepath, patched_rrsync, rmtree, rsync_argv, rsync_path_arg

base = SCRATCHDIR / 'rrsync-merge-file-confine'
rmtree(base)
root = base / 'root'
sub = root / 'sub'
dest = base / 'dest'
makepath(sub, dest)

# The secret sits BESIDE the restricted dir, so "../" reaches it from the root.
# Its line is the name of a file inside the root: if the server reads it as an
# exclude list, that file silently vanishes from the transfer and the client has
# confirmed the content without ever seeing it.
(base / 'outside-secret').write_text('secretword\n')
(root / 'secretword').write_bytes(b'X')
(root / 'keep').write_bytes(b'X')
# An ordinary in-tree merge file, for the control below.
(sub / 'intree-dropped').write_bytes(b'X')
(sub / 'intree-kept').write_bytes(b'X')
(sub / '.rsync-filter').write_text('intree-dropped\n')

# RSYNC may be a multi-word command (the runner's --protocol=N, or valgrind)
# while rrsync hands its RSYNC to execlp() as a single executable name, so it
# has to be wrapped before patched_rrsync() sees it.
shim = base / 'rsync-shim'
shim.write_text('#!/bin/sh\nexec ' + rsync_path_arg(RSYNC) + ' "$@"\n')
shim.chmod(0o755)
wrapper = patched_rrsync(base, rsync_path=str(shim))
rsh = f'{shlex.quote(sys.executable)} {shlex.quote(os.path.abspath(__file__))} --shell'
env = {**os.environ, 'RRSYNC_WRAPPER': str(wrapper), 'RRSYNC_ROOT': str(root)}


def pull(filt, into):
    into.mkdir(parents=True, exist_ok=True)
    return subprocess.run(
        rsync_argv('-r', f'--filter={filt}', '-e', rsh, 'ignored:.', f'{into}/'),
        env=env, capture_output=True, text=True, timeout=20)


# The escape: an exclude-only dir-merge naming a file outside the restricted
# dir.  No --delete and no -v are involved -- the answer is the file list.
esc = dest / 'escape'
got = pull(':-s ../outside-secret', esc)
assert got.returncode == 0, (
    'confining the merge open must not cost the transfer: refusing outright '
    f'would hand the peer a denial of service. rc={got.returncode}, '
    f'stderr={got.stderr!r}')
assert (esc / 'secretword').exists(), (
    'the server read a merge file outside the restricted dir: "secretword" was '
    f'excluded, so its presence in that file is confirmed to the client. '
    f'rc={got.returncode}, stdout={got.stdout!r}, stderr={got.stderr!r}')
assert 'secretword' not in got.stderr, (
    f'the out-of-root merge file\'s text reached the peer: stderr={got.stderr!r}')

# Control: an ordinary in-tree .rsync-filter must still be read and obeyed.
# Without this, refusing every merge file would pass the assertion above.
ctl = dest / 'control'
got = pull(':-s .rsync-filter', ctl)
assert got.returncode == 0, (
    f'the in-tree dir-merge failed the transfer: rc={got.returncode}, '
    f'stderr={got.stderr!r}')
assert (ctl / 'sub' / 'intree-kept').exists(), (
    f'the in-tree dir-merge broke the transfer: rc={got.returncode}, '
    f'stdout={got.stdout!r}, stderr={got.stderr!r}')
assert not (ctl / 'sub' / 'intree-dropped').exists(), (
    'the in-tree .rsync-filter was not applied, so this control does not show '
    f'that merge files still work: stdout={got.stdout!r}, stderr={got.stderr!r}')

# --confine-root direct, no wrapper: a source argument reached through a symlink
# makes rsync's tracked cwd deeper than the kernel's, so a merge file's own
# relative symlink resolves from one directory while the confinement check walks
# from another.  Seeded lexically, a ".." that really escapes looks like it
# landed back inside the root.  rrsync happens to reject the argument shapes
# that reach this, so it is driven directly rather than through the wrapper.
alias_root = base / 'aliased'
makepath(alias_root)
(base / 'aliased-secret').write_text('aliased-word\n')
(alias_root / 'aliased-word').write_bytes(b'X')
(alias_root / 'plain').write_bytes(b'X')
os.symlink('.', alias_root / 'self-alias')
os.symlink('../aliased-secret', alias_root / 'outside-link')
(alias_root / 'intree-list').write_text('plain\n')


def confined_pull(filter_text, into):
    (alias_root / '.rsync-filter').write_text(filter_text + '\n')
    rmtree(into)
    into.mkdir(parents=True, exist_ok=True)
    return subprocess.run(
        rsync_argv('-r', f'--confine-root={alias_root}',
                   '--filter=:s .rsync-filter',
                   f'{alias_root}/self-alias/', f'{into}/'),
        capture_output=True, text=True, timeout=20)


esc2 = dest / 'aliased-escape'
got = confined_pull('merge,- outside-link', esc2)
assert not (got.returncode == 0 and not (esc2 / 'aliased-word').exists()), (
    'a merge file outside the root was read through a symlinked source arg, so '
    f'"aliased-word" was excluded: rc={got.returncode}, stderr={got.stderr!r}')

# Control: the same symlinked source arg with an IN-TREE merge target must still
# work, or the assertion above would also pass if confinement simply broke every
# transfer that reaches a directory through a symlink.
ctl2 = dest / 'aliased-control'
got = confined_pull('merge,- intree-list', ctl2)
assert got.returncode == 0 and (ctl2 / 'aliased-word').exists(), (
    'a symlinked source arg with an in-tree merge file broke the transfer: '
    f'rc={got.returncode}, stderr={got.stderr!r}')
assert not (ctl2 / 'plain').exists(), (
    'the in-tree merge file was not applied through the symlinked source arg, '
    f'so the escape check above proves nothing: stderr={got.stderr!r}')

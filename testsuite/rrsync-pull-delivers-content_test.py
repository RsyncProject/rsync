#!/usr/bin/env python3
"""A pull through rrsync must deliver the file's CONTENT.

rrsync inode-pins each validated transfer argument and rewrites the argument to
/proc/self/fd/N so the exec'd rsync re-resolves it to the pinned inode.  That is
right for the receiver, which plain-open()s its destination -- but the sender
never plain-opens the transfer argument: send_file_list() lstat()s it, and
lstat() of a /proc/self/fd magic link always reports S_IFLNK.  So the sender
described the argument as a symlink instead of sending the file.

The observable damage depends on how much the receiver checks.  It has been seen
both as a delivered dangling symlink holding the server's absolute in-tree path
and no content, and -- with the current receiver's file-list name check -- as an
outright refusal:

    ERROR: rejecting unrequested file-list name: 4

Either way the canonical `rsync -a host:file dest/` through a restricted account
does not deliver the file, so this asserts the user-visible outcome (content
arrives, as a regular file) rather than any one symptom.

The push case is checked too: the fix must not be achievable by abandoning the
receiver-side pinning that the same code does for real.
"""

import os
import shlex
import subprocess

from rsyncfns import (
    RSYNC, SCRATCHDIR, makepath, patched_rrsync, rmtree, rsync_argv, test_fail, rsync_path_arg,
)

PULL_DATA = 'REAL-FILE-CONTENT\n'
PUSH_DATA = 'PUSHED-FILE-CONTENT\n'

base = SCRATCHDIR / 'rrsync-pull-content'
rmtree(base)
restricted = base / 'restricted'
dest = base / 'dest'
src = base / 'src'
makepath(restricted, dest, src)

(restricted / 'f1').write_text(PULL_DATA)
(src / 'up1').write_text(PUSH_DATA)
# rrsync refuses a --sender arg that does not already exist, so the push target
# directory has to be there before the transfer.
makepath(restricted / 'inbox')

# $RSYNC may carry a wrapper (valgrind, --protocol=N); rrsync wants a single
# executable, so hand it a shim that re-expands the whole command line.
shim = base / 'rsync-shim'
shim.write_text('#!/bin/sh\nexec ' + rsync_path_arg(RSYNC) + ' "$@"\n')
shim.chmod(0o755)

rrsync = patched_rrsync(base, rsync_path=str(shim))

# Stand in for ssh: drop the host argument and pass the rest to rrsync exactly
# as sshd would, through SSH_ORIGINAL_COMMAND.
rsh = base / 'fake-rsh'
rsh.write_text(
    '#!/bin/sh\n'
    'shift\n'
    'SSH_ORIGINAL_COMMAND="$*"\n'
    'export SSH_ORIGINAL_COMMAND\n'
    'exec %s %s\n' % (shlex.quote(str(rrsync)), shlex.quote(str(restricted))))
rsh.chmod(0o755)


def transfer(*args):
    return subprocess.run(rsync_argv('-a', '-e', str(rsh), *args),
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          text=True)


# --- the regression: a plain pull of a file ---------------------------------
proc = transfer('dummy:f1', str(dest) + '/')
ctx = f'rc={proc.returncode}, output={proc.stdout.strip()[:300]!r}'
got = dest / 'f1'

if got.is_symlink():
    test_fail(f'pull delivered a symlink instead of the file: '
              f'{os.readlink(got)!r} ({ctx})')
if not got.exists():
    test_fail(f'pull delivered nothing ({ctx})')
if not got.is_file():
    test_fail(f'pull delivered a non-regular file ({ctx})')
if got.read_text() != PULL_DATA:
    test_fail(f'pull delivered the wrong content: {got.read_text()!r} ({ctx})')
if proc.returncode != 0:
    test_fail(f'pull delivered the content but failed ({ctx})')

# --- control: the receiver direction still works ----------------------------
# Without this, "stop handing the sender a pinned fd" could be satisfied by
# dropping the pinning altogether, which is the hardening this must preserve.
proc = transfer(str(src) + '/up1', 'dummy:inbox/')
ctx = f'rc={proc.returncode}, output={proc.stdout.strip()[:300]!r}'
landed = restricted / 'inbox' / 'up1'

if proc.returncode != 0:
    test_fail(f'push through rrsync failed ({ctx})')
if not landed.is_file() or landed.read_text() != PUSH_DATA:
    test_fail(f'push did not deliver the content ({ctx})')

print('rrsync pull delivers file content; push still works')

#!/usr/bin/env python3
"""A pull with a local --files-from must work through rrsync.

`rsync -a --files-from=LIST host:. dest/` does not send the list file to the
server.  It sends the literal `--files-from=-` and streams the names down the
protocol connection, so `-` reaches rrsync as an option value meaning "read the
list from stdin" -- it is a sentinel, not a pathname.

88cee089 started inode-pinning every checked option value, and `--files-from`
is checked (long_opts['files-from'] == 3).  Pinning `-` means realpath()ing and
opening a file named "-" in the restricted dir, which does not exist, so rrsync
kills the connection before rsync ever runs:

    rrsync error: post-realpath open failed (race detected): - No such file...

That breaks every --files-from pull through a restricted account.  v3.4.4
delivers the file.

A list file that really is a pathname must still be validated and pinned, so
the second half checks that an out-of-tree --files-from is still refused.
"""

import os
import shlex
import subprocess

from rsyncfns import (
    RSYNC, SCRATCHDIR, makepath, patched_rrsync, rmtree, rsync_argv, test_fail, rsync_path_arg,
)

DATA = 'FILES-FROM-CONTENT\n'

base = SCRATCHDIR / 'rrsync-files-from'
rmtree(base)
restricted = base / 'restricted'
dest = base / 'dest'
outside = base / 'outside'
makepath(restricted, dest, outside)

(restricted / 'f1').write_text(DATA)
(restricted / 'f2').write_text(DATA)
(outside / 'list').write_text('f1\n')

listfile = base / 'list'
listfile.write_text('f1\nf2\n')

# $RSYNC may carry a wrapper (valgrind, --protocol=N); rrsync wants a single
# executable, so hand it a shim that re-expands the whole command line.
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


def transfer(*args):
    return subprocess.run(rsync_argv('-a', '-e', str(rsh), *args),
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          text=True)


# --- the regression: a local --files-from sends "--files-from=-" -------------
proc = transfer('--files-from=' + str(listfile), 'dummy:.', str(dest) + '/')
ctx = f'rc={proc.returncode}, output={proc.stdout.strip()[:300]!r}'

for name in ('f1', 'f2'):
    got = dest / name
    if not got.is_file():
        test_fail(f'--files-from pull did not deliver {name} ({ctx})')
    if got.read_text() != DATA:
        test_fail(f'--files-from pull delivered wrong content for {name} ({ctx})')
if proc.returncode != 0:
    test_fail(f'--files-from pull delivered the files but failed ({ctx})')

# --- control: a --files-from that IS a pathname stays confined ---------------
# The sentinel exemption must be for the exact string "-" only, not a general
# hole in the checking of this option.
def rrsync_direct(files_from):
    # rrsync insists on the "--sender ..." shape sshd hands it; get that right
    # or it dies at the syntax check without ever looking at the option value,
    # which would make this control pass no matter what.
    return subprocess.run(
        [str(rrsync), '-ro', '-no-lock', str(restricted)],
        env={**os.environ,
             'SSH_ORIGINAL_COMMAND':
                 'rsync --server --sender -logDtpre.iLsfxC '
                 '--files-from=%s . .' % files_from},
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)


# Prove the command shape itself is accepted, so a rejection below is really
# about the pathname.
proc = rrsync_direct(restricted / 'f1')
for wrong in ('invalid rsync-command syntax', 'does not run rsync'):
    if wrong in proc.stdout:
        test_fail('the control command shape is wrong, so it never reaches the '
                  f'--files-from check ({wrong!r}): '
                  f'output={proc.stdout.strip()[:300]!r}')

proc = rrsync_direct(outside / 'list')
if proc.returncode == 0:
    test_fail('an out-of-tree --files-from pathname was accepted: '
              f'output={proc.stdout.strip()[:300]!r}')
for wrong in ('invalid rsync-command syntax', 'does not run rsync'):
    if wrong in proc.stdout:
        test_fail('the out-of-tree --files-from was rejected for the wrong '
                  f'reason ({wrong!r}, not the pathname): '
                  f'{proc.stdout.strip()[:300]!r}')

print('rrsync passes the --files-from stdin sentinel through; '
      'a real out-of-tree list file is still refused')

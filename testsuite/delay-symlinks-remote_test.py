#!/usr/bin/env python3
# Coverage of how --delay-symlinks is passed between client and server.
#
# Only the receiving side defers symlinks, so server_options() passes the
# option to a remote receiver (a push) but not to a remote sender (a pull).
# Either way the receiver turns off incremental recursion, which -v shows by
# not printing "sending/receiving incremental file list".  Below protocol 29
# the option is an error, and a remote receiver too old to know the option
# rejects it before anything is transferred.

import os
import subprocess
import sys

from rsyncfns import (
    FROMDIR, SCRATCHDIR, SRCDIR, TODIR,
    forced_protocol, rmtree, rsh_cmd, rsync_path_arg, run_rsync, test_fail,
    test_skipped,
)

proto = forced_protocol()
if proto is not None and proto < 29:
    test_skipped(f"--delay-symlinks requires protocol 29+ (negotiated {proto})")
# Incremental recursion needs protocol 30, so below that there's none to
# turn off.
inc_recurse = proto is None or proto >= 30

# A remote shell that records the command it is asked to run, then runs it
# the way support/lsh.sh does.
LOG = SCRATCHDIR / 'remote-commands'
RSH = SCRATCHDIR / 'logging-rsh'
RSH.write_text(f'''#!{sys.executable}
import os, sys
with open({str(LOG)!r}, 'a') as fh:
    fh.write(' '.join(sys.argv[1:]) + '\\n')
os.execv('/bin/sh', ['/bin/sh', {str(SRCDIR / 'support' / 'lsh.sh')!r},
                     *sys.argv[1:]])
''')
RSH.chmod(0o755)

rmtree(FROMDIR)
(FROMDIR / 'sub').mkdir(parents=True)
(FROMDIR / 'sub' / 'target').write_text("target\n")
os.symlink('target', FROMDIR / 'sub' / 'link')


def remote_run(direction, *opts, rsync_path=None):
    """Push or pull FROMDIR to TODIR through the logging remote shell.
    Returns the finished process and the remote command it ran."""
    rmtree(TODIR)
    if LOG.exists():
        LOG.unlink()
    if direction == 'push':
        paths = (f'{FROMDIR}/', f'lh:{TODIR}/')
    else:
        paths = (f'lh:{FROMDIR}/', f'{TODIR}/')
    proc = run_rsync('-rlv', *opts, f'--rsh={rsh_cmd(str(RSH))}',
                     f'--rsync-path={rsync_path or rsync_path_arg()}', *paths,
                     check=False, capture_output=True)
    return proc, LOG.read_text() if LOG.exists() else ''


for direction in ('push', 'pull'):
    # Control: without the option the transfer recurses incrementally.
    proc, remote = remote_run(direction)
    if proc.returncode != 0:
        test_fail(f"{direction}: control transfer exited {proc.returncode}:\n"
                  f"{proc.stderr}")
    if inc_recurse and 'incremental file list' not in proc.stdout:
        test_fail(f"{direction}: the control transfer didn't recurse "
                  f"incrementally, so the check below proves nothing:\n"
                  f"{proc.stdout}")

    proc, remote = remote_run(direction, '--delay-symlinks')
    if proc.returncode != 0:
        test_fail(f"{direction}: transfer exited {proc.returncode}:\n"
                  f"{proc.stderr}")
    if 'incremental file list' in proc.stdout:
        test_fail(f"{direction}: --delay-symlinks didn't turn off incremental "
                  f"recursion:\n{proc.stdout}")
    if ('--delay-symlinks' in remote) != (direction == 'push'):
        whom = 'remote receiver' if direction == 'push' else 'remote sender'
        test_fail(f"{direction}: the option should be passed only to a remote "
                  f"receiver, but the {whom} was run as: {remote}")
    if os.readlink(TODIR / 'sub' / 'link') != 'target':
        test_fail(f"{direction}: the symlink wasn't transferred")

# Below protocol 29 the option is an error, locally and for a push.
for paths in ((f'{FROMDIR}/', f'{TODIR}/'), (f'{FROMDIR}/', f'lh:{TODIR}/')):
    rmtree(TODIR)
    proc = run_rsync('-rl', '--delay-symlinks', '--protocol=28',
                     f'--rsh={rsh_cmd()}', f'--rsync-path={rsync_path_arg()}',
                     *paths, check=False, capture_output=True)
    if (proc.returncode != 2
            or 'requires protocol 29 or higher' not in proc.stderr):
        test_fail(f"--protocol=28 to {paths[1]}: expected the protocol error "
                  f"(exit 2), got exit {proc.returncode}:\n{proc.stderr}")
    if os.path.lexists(TODIR / 'sub' / 'link'):
        test_fail(f"--protocol=28 to {paths[1]}: the transfer went ahead")

# A remote rsync from before the option rejects it when it is the receiver,
# before anything is transferred, and isn't sent it when it is the sender.
old = SRCDIR / 'old_versions' / 'rsync_3.4.1'
try:
    runnable = subprocess.run([str(old), '--version'], capture_output=True,
                              timeout=10).returncode == 0
except (OSError, subprocess.TimeoutExpired):
    runnable = False
if runnable:
    old_path = rsync_path_arg(str(old))
    proc, remote = remote_run('push', '--delay-symlinks', rsync_path=old_path)
    if (proc.returncode == 0
            or '--delay-symlinks: unknown option' not in proc.stderr):
        test_fail(f"push to rsync 3.4.1: expected it to reject the option, got "
                  f"exit {proc.returncode}:\n{proc.stderr}")
    if os.path.lexists(TODIR / 'sub'):
        test_fail("push to rsync 3.4.1: files were transferred anyway")

    proc, remote = remote_run('pull', '--delay-symlinks', rsync_path=old_path)
    if proc.returncode != 0:
        test_fail(f"pull from rsync 3.4.1: exited {proc.returncode}:\n"
                  f"{proc.stderr}")
    if os.readlink(TODIR / 'sub' / 'link') != 'target':
        test_fail("pull from rsync 3.4.1: the symlink wasn't transferred")

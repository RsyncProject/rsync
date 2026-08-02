#!/usr/bin/env python3
"""A raced LAST component must not leak outside content through an rrsync pull.

rrsync inode-pins the components above a sender argument's last one, but not
always the last one itself: rsync lstat()s a source argument, and lstat of a
/proc/self/fd magic link is always S_IFLNK, so handing it the leaf's pin makes
the sender describe the argument as a symlink and send no data at all (see
rrsync-pull-delivers-content).

What keeps that safe is rsync's own behaviour rather than the wrapper's:

  * a symlink at a plain source argument is transmitted AS a symlink -- the
    sender does not read through it;
  * the options that would change that (-L, -k, --copy-unsafe-links) are
    disabled by rrsync for a restricted dir, and --insecure-links is not in its
    allowlist at all;
  * the sender's own opens are O_NOFOLLOW, and directories go through
    secure_opendir(), so a flip landing between rsync's lstat() and its open is
    refused by the kernel.

So this drives the real binary and asserts the property that actually holds --
no outside CONTENT reaches the client -- rather than requiring a particular
symptom from a race that may or may not be won on any given run.

Note what is NOT claimed: an outside symlink's target string can still be
disclosed, since transmitting the symlink is the correct behaviour for the
shape. rrsync's -munge blunts that. Under --relative the argument is anchored
higher, so the intermediate components are exposed to the same window; that is
a known limitation of the wrapper, recorded in the plan notes, not something
this test asserts away.
"""

import os
import shlex
import subprocess
import time

from rsyncfns import (
    race_budget, RSYNC, SCRATCHDIR, makepath, patched_rrsync,
    proc_self_fd_pins, rmtree, rsync_argv, start_path_flipper, stop_flipper,
    test_fail, test_skipped, rsync_path_arg,
)

if not proc_self_fd_pins():
    test_skipped("rrsync's realpath-vs-exec inode-pin needs /proc/self/fd "
                 "(Linux); unhardened fallback elsewhere by design")

MARKER = 'AUDIT_RRSYNC_LEAF_FLIP_MARKER_DO_NOT_DISCLOSE'

base = SCRATCHDIR / 'rrsync-leaf-flip'
rmtree(base)
restricted = base / 'restricted'
outside = base / 'outside'
dest = base / 'dest'
makepath(restricted, outside / 'dir', dest)

(outside / 'secret').write_text(MARKER + '\n')
(outside / 'dir' / 'loot').write_text(MARKER + '\n')

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
    'exec %s -ro %s\n' % (shlex.quote(str(rrsync)), shlex.quote(str(restricted))))
rsh.chmod(0o755)


def leaked_content():
    """True if any delivered regular file holds the outside marker."""
    for path in dest.rglob('*'):
        if path.is_file() and not path.is_symlink():
            try:
                if MARKER in path.read_text():
                    return str(path.relative_to(dest))
            except (OSError, UnicodeDecodeError):
                pass
    return None


def race_pull(source, real_name, evil_name):
    flip = start_path_flipper(real_name, evil_name)
    try:
        deadline = time.monotonic() + race_budget()
        while time.monotonic() < deadline:
            rmtree(dest)
            dest.mkdir()
            subprocess.run(rsync_argv('-a', '-e', str(rsh), source,
                                      str(dest) + '/'),
                           stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL)
            got = leaked_content()
            if got:
                return got
    finally:
        stop_flipper(flip)
    return None


# --- a plain file argument whose leaf flips to an outside symlink ------------
real_file = restricted / 'target'
real_file.write_text('benign_in_tree_content\n')
evil_file = restricted / 'evil'
os.symlink(str(outside / 'secret'), evil_file)

got = race_pull('dummy:target', real_file, evil_file)
if got:
    test_fail(f'a raced leaf flip leaked outside content to {got!r}: the '
              'sender read through a symlink substituted at the source '
              'argument after rrsync validated it')

rmtree(real_file)
rmtree(evil_file)

# --- a trailing-slash directory, which rsync DOES follow ---------------------
# This shape keeps the leaf pin precisely because rsync opens it rather than
# lstat()ing it; without the pin the flip below transfers outside/dir/loot.
real_dir = restricted / 'dir'
makepath(real_dir)
(real_dir / 'ok').write_text('benign\n')
evil_dir = restricted / 'evildir'
os.symlink(str(outside / 'dir'), evil_dir)

got = race_pull('dummy:dir/', real_dir, evil_dir)
if got:
    test_fail(f'a raced trailing-slash directory flip leaked outside content '
              f'to {got!r}: the leaf pin did not hold')

print('rrsync sender pulls leak no outside content when the last component is raced')

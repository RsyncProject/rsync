#!/usr/bin/env python3
# Daemon module-exclude bypass via a DEEPER in-tree directory symlink (not the
# destination argument).  (Probe for codex's "per-chdir physical tracking"
# concern.)
#
# The module excludes a nested subtree exclude = /pub/secret/.  pub/ is NOT
# excluded.  An in-module dir symlink  dir2 -> pub/  sits inside the dest tree
# (a level below the dest root).  A peer pushes a tree whose file list contains
# dir2/secret/x and runs with --keep-dirlinks, so the receiver follows dir2 into
# pub while descending, then writes secret/x into pub/secret/x -- the excluded
# subtree -- if the exclude is checked on the logical "dir2/secret" rather than
# the physical "pub/secret".
#
# This differs from symlink-exclude-component (one level, leaf parent) and
# symlink-exclude-chdir-alias (the symlink IS the dest arg): here the followed
# symlink is a deeper subdirectory reached as the transfer descends.  RED here
# means the held-dirfd seed alone is NOT enough and per-chdir physical tracking
# is required.  Runs unprivileged.
import os
import subprocess

from rsyncfns import (
    SCRATCHDIR, rmtree, rsync_argv, start_test_daemon, test_fail, write_daemon_conf,
)

DAEMON_PORT = 12983
SECRET = "PROTECTED-IN-EXCLUDED-SUBTREE\n"

base = SCRATCHDIR / 'symlink-exclude-deep'
rmtree(base)
base.mkdir()

mod = base / 'mod'
mod.mkdir()
pub = mod / 'pub'                              # NOT excluded
pub.mkdir()
secret = pub / 'secret'                        # excluded: /pub/secret/
secret.mkdir()
victim = secret / 'x'
victim.write_text(SECRET)

os.symlink('pub/', mod / 'dir2')               # in-tree dir symlink -> pub/

src = base / 'src'
(src / 'dir2' / 'secret').mkdir(parents=True)
(src / 'dir2' / 'secret' / 'x').write_text("NEW\n")

conf = write_daemon_conf(
    [('mod', {'path': str(mod), 'read only': 'no', 'exclude': '/pub/secret/'})])
url = start_test_daemon(conf, DAEMON_PORT)

subprocess.run(
    rsync_argv('-a', '--keep-dirlinks', f'{src}/', f'{url}mod/'),
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

after = victim.read_text() if victim.exists() else None
if after == SECRET:
    test_fail(
        "the daemon refused a deeper in-tree dir symlink that stock rsync (3.2.7) "
        f"follows: a push to dir2/secret/x did not reach pub/secret/ ({victim} is "
        f"still {after!r}).  The exclude is name-based ('dir2/secret', not the "
        "physical 'pub/secret'); it must not block this.")
print("daemon exclude is name-based: a deeper in-tree dir symlink reaches the "
      "nested excluded subtree (3.2.7-equivalent)")

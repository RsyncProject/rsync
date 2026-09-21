#!/usr/bin/env python3
# A daemon must not let a peer-supplied --backup-dir reach the module's EXCLUDED
# subtree via a symlink.  (Reported by Omar Elsayed.)
#
# The module excludes a subtree (exclude = /secret/).  An in-module symlink owned
# by the daemon's euid (in Omar's PoC the daemon is root and the symlink is
# root-owned) points at that excluded subtree.  A peer overwrites an existing
# dest file with --backup --backup-dir pointing at the symlink: the OLD dest file
# is moved into the backup dir, i.e. through the symlink into the excluded
# subtree -- scribbling on (or destroying) a file the module does not serve.
#
# The ownership walk that make_backup() already uses does NOT stop this: it
# FOLLOWS a symlink owned by uid 0 / the euid by design.  Only resolving with the
# module exclude filter applied refuses landing in the excluded subtree.  So the
# symlink here is euid-owned (the followed case), and the test runs unprivileged
# -- the boundary under test is the module exclude, not a uid boundary.

import os
import subprocess

from rsyncfns import (
    SCRATCHDIR, rmtree, rsync_argv, start_test_daemon, test_fail, write_daemon_conf,
)

DAEMON_PORT = 12904
SECRET = "PROTECTED-IN-EXCLUDED-SUBTREE\n"

base = SCRATCHDIR / 'backupdirdaemon'
rmtree(base)
base.mkdir()

mod = base / 'mod'
secret = mod / 'secret'                      # excluded subtree (exclude = /secret/)
secret.mkdir(parents=True)
victim = secret / 'f0'
victim.write_text(SECRET)                    # a file the backup must not clobber

# Existing dest file (different size from the source) so the overwrite triggers a
# backup; src/f0 differs so the transfer actually happens.
(mod / 'f0').write_text("OLD-DESTINATION-FILE-CONTENT\n")

blink = mod / 'blink'                        # euid-owned symlink -> excluded subtree
os.symlink('secret', blink)

src = base / 'src'
src.mkdir()
(src / 'f0').write_text("NEW\n")

conf = write_daemon_conf(
    [('mod', {'path': str(mod), 'read only': 'no', 'exclude': '/secret/'})])
url = start_test_daemon(conf, DAEMON_PORT)

subprocess.run(
    rsync_argv('-a', '--backup', '--backup-dir=/blink', f'{src}/', f'{url}mod/'),
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

after = victim.read_text() if victim.exists() else None
if after == SECRET:
    test_fail(
        "the daemon refused a --backup-dir symlink that stock rsync (3.2.7) "
        f"follows: {victim} is unchanged ({after!r}).  The daemon exclude filter "
        "is name-based ('blink' is not excluded), not a symlink boundary; it must "
        "not block this (use `munge symlinks` to defend a writable module).")
print("daemon exclude is name-based: a --backup-dir symlink whose name is not "
      "excluded is followed (3.2.7-equivalent)")

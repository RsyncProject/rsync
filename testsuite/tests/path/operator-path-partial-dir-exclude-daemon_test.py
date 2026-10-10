#!/usr/bin/env python3
# A daemon must not let a peer-supplied --partial-dir reach the module's EXCLUDED
# subtree via a symlink.  (Reported by Omar Elsayed.)
#
# The module excludes a subtree (exclude = /secret/).  An in-module symlink owned
# by the daemon's euid (in Omar's PoC the daemon is root and the symlink is
# root-owned) points at that excluded subtree.  A peer transfers with
# --partial-dir pointing at the symlink: the received file is staged through the
# symlink into the excluded subtree and the existing file there is overwritten /
# renamed away -- destroying a file the module does not serve.
#
# secure_relative_open() confines beneath the module root but FOLLOWS in-module
# symlinks, so it does not stop this; only resolving with the module exclude
# filter applied refuses landing in the excluded subtree.  The symlink is euid-
# owned (the followed case) and the test runs unprivileged -- the boundary under
# test is the module exclude, not a uid boundary.

import os
import subprocess

from rsyncfns import (
    SCRATCHDIR, rmtree, rsync_argv, start_test_daemon, test_fail, write_daemon_conf,
)

DAEMON_PORT = 12903
SECRET = "PROTECTED-IN-EXCLUDED-SUBTREE\n"

base = SCRATCHDIR / 'partialdirdaemon'
rmtree(base)
base.mkdir()

mod = base / 'mod'
secret = mod / 'secret'                      # excluded subtree (exclude = /secret/)
secret.mkdir(parents=True)
victim = secret / 'f0'
victim.write_text(SECRET)                    # a file the staging must not destroy

blink = mod / 'blink'                        # euid-owned symlink -> excluded subtree
os.symlink('secret', blink)

src = base / 'src'
src.mkdir()
(src / 'f0').write_text("NEW-PUSHED-CONTENT\n")   # same basename as the protected file

conf = write_daemon_conf(
    [('mod', {'path': str(mod), 'read only': 'no', 'exclude': '/secret/'})])
url = start_test_daemon(conf, DAEMON_PORT)

# partial_dir_fname() builds <partial-dir>/f0, which the basis/staging path opens
# (through the symlink) and then renames/unlinks.
subprocess.run(
    rsync_argv('-a', '--partial-dir=/blink', f'{src}/', f'{url}mod/'),
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

after = victim.read_text() if victim.exists() else None
if after == SECRET:
    test_fail(
        "the daemon refused a --partial-dir symlink that stock rsync (3.2.7) "
        f"follows: {victim} is unchanged ({after!r}).  The daemon exclude filter "
        "is name-based ('blink' is not excluded), not a symlink boundary; it must "
        "not block this (use `munge symlinks` to defend a writable module).")
print("daemon exclude is name-based: a --partial-dir symlink whose name is not "
      "excluded is followed (3.2.7-equivalent)")

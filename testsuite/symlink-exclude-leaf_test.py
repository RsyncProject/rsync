#!/usr/bin/env python3
# Daemon module-exclude bypass on an excluded LEAF (file) reached through an
# in-tree directory symlink.  (Reported by codex.)
#
# Unlike the subtree cases, the exclude here names a single leaf: /pub/blocked.
# pub/ itself is NOT excluded, so resolving the parent through the in-module
# symlink  blink2 -> pub/  does not refuse it; only the final basename is
# excluded.  A peer sends blink2/blocked under --keep-dirlinks: the held-dirfd
# resolves the parent to pub and the leaf create/rename writes pub/blocked --
# the excluded file -- because the leaf basename is never physically
# exclude-checked.  Runs unprivileged.
import os
import subprocess

from rsyncfns import (
    SCRATCHDIR, rmtree, rsync_argv, start_test_daemon, test_fail, write_daemon_conf,
)

DAEMON_PORT = 12987

base = SCRATCHDIR / 'symlink-exclude-leaf'
rmtree(base)
base.mkdir()

mod = base / 'mod'
mod.mkdir()
pub = mod / 'pub'                              # NOT excluded
pub.mkdir()
os.symlink('pub/', mod / 'blink2')             # in-tree dir symlink -> pub/

src = base / 'src'
(src / 'blink2').mkdir(parents=True)
(src / 'blink2' / 'blocked').write_text("SHOULD-NOT-LAND\n")

conf = write_daemon_conf(
    [('mod', {'path': str(mod), 'read only': 'no', 'exclude': '/pub/blocked'})])
url = start_test_daemon(conf, DAEMON_PORT)

subprocess.run(
    rsync_argv('-a', '--keep-dirlinks', f'{src}/', f'{url}mod/'),
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

blocked = pub / 'blocked'
if not blocked.exists():
    test_fail(
        "the daemon refused an excluded leaf that stock rsync (3.2.7) writes: "
        f"{blocked} was not created through the in-tree symlink.  The exclude is "
        "name-based (it sees 'blink2/blocked', not the physical 'pub/blocked'); it "
        "must not block this.")
print("daemon exclude is name-based: an excluded leaf reached through an in-tree "
      "dir symlink is written (3.2.7-equivalent)")

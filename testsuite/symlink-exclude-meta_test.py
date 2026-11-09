#!/usr/bin/env python3
# Daemon module-exclude bypass: metadata write on a PRE-EXISTING excluded leaf
# reached through an in-tree directory symlink.  (Reported by codex.)
#
# No create is needed: pub/blocked already exists and is excluded (/pub/blocked).
# pub/ is not excluded, so resolving the parent through blink2 -> pub/ succeeds;
# the generator can then stat the leaf through the held parent fd and push
# metadata (chmod/chown/times) onto it via the held-dirfd metadata ops.  Those
# must refuse an excluded physical leaf, or a peer can alter a file the module
# does not serve.  Runs unprivileged (mtime is the observable a non-root peer can
# drive).
import os
import subprocess
import time

from rsyncfns import (
    SCRATCHDIR, rmtree, rsync_argv, start_test_daemon, test_fail, write_daemon_conf,
)

DAEMON_PORT = 12989

base = SCRATCHDIR / 'symlink-exclude-meta'
rmtree(base)
base.mkdir()

mod = base / 'mod'
mod.mkdir()
pub = mod / 'pub'                              # NOT excluded
pub.mkdir()
victim = pub / 'blocked'                       # excluded leaf, pre-exists
victim.write_text("PROTECTED\n")
old = 1_000_000_000
os.utime(victim, (old, old))                   # known baseline mtime
os.symlink('pub/', mod / 'blink2')             # in-tree dir symlink -> pub/

src = base / 'src'
(src / 'blink2').mkdir(parents=True)
(src / 'blink2' / 'blocked').write_text("PROTECTED\n")   # same content: only meta differs
new = old + 999_999
os.utime(src / 'blink2' / 'blocked', (new, new))

conf = write_daemon_conf(
    [('mod', {'path': str(mod), 'read only': 'no', 'exclude': '/pub/blocked'})])
url = start_test_daemon(conf, DAEMON_PORT)

subprocess.run(
    rsync_argv('-a', '--keep-dirlinks', f'{src}/', f'{url}mod/'),
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

if int(victim.stat().st_mtime) == old:
    test_fail(
        "the daemon refused a metadata write onto an excluded leaf that stock "
        f"rsync (3.2.7) performs: {victim} mtime is still {old} (expected it to "
        f"advance to {new}).  The exclude is name-based ('blink2/blocked', not the "
        "physical 'pub/blocked'); it must not block this.")
print("daemon exclude is name-based: metadata on an excluded leaf via an in-tree "
      "symlink is written (3.2.7-equivalent)")

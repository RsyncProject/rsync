#!/usr/bin/env python3
# The daemon exclude/filter is a NAME-based listing/tamper filter, not a
# physical-path security boundary: a peer that pushes through an in-module symlink
# whose own name is not excluded reaches the excluded target.  This is the
# behaviour of stock rsync (verified against 3.2.7) and is documented in
# rsyncd.conf(5) -- the symlink defense is `munge symlinks` (default on for a
# writable non-chroot module), NOT the filter.  This test asserts that
# 3.2.7-equivalent behaviour: the symlink IS followed.

import os
import subprocess

from rsyncfns import (
    SCRATCHDIR, rmtree, rsync_argv, start_test_daemon, test_fail, write_daemon_conf,
)

DAEMON_PORT = 12962

base = SCRATCHDIR / 'symlink-exclude-daemon'
rmtree(base)
base.mkdir()

mod = base / 'mod'
mod.mkdir()

secret = mod / 'secret'
secret.mkdir(parents=True)
victim = secret / 'f0'
victim.write_text("OLD-EXCLUDED-CONTENT\n")
src = mod / 'src'
src.mkdir()
(src / 'f0').write_text("NEW\n")

blink = mod / 'blink'                        # in-module symlink -> excluded subtree
os.symlink('secret/', blink)

conf = write_daemon_conf(
    [('mod', {'path': str(mod), 'read only': 'no', 'exclude': '/secret/'})])
url = start_test_daemon(conf, DAEMON_PORT)

# Push through the symlink whose own name ("blink") is not excluded.
subprocess.run(
    rsync_argv('-a', f'{src}/', f'{url}mod/blink/'),
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

after = victim.read_text() if victim.exists() else None
if after != "NEW\n":
    test_fail(
        "the daemon refused a symlink that stock rsync (3.2.7) follows: "
        f"{secret / 'f0'} is {after!r}, expected 'NEW\\n'.  The daemon exclude "
        "filter is name-based, not a symlink boundary -- it must not block this "
        "(use `munge symlinks` to defend a writable module).")
print("daemon exclude is name-based: a symlink whose name is not excluded is "
      "followed (3.2.7-equivalent)")

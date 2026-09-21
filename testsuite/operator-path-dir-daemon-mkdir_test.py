#!/usr/bin/env python3
# A DIRECTORY-only module exclude (exclude = /public/pdir/) must not be
# bypassable by creating that dir through a peer --partial-dir symlink.
# (Codex-review follow-up.)  The partial dir does not exist yet, so the leaf is
# created by handle_partial_dir(); the resolver must filter-check the absent
# leaf against dir-only rules, not just as a file.  Runs unprivileged.

import os
import subprocess

from rsyncfns import (
    SCRATCHDIR, rmtree, rsync_argv, start_test_daemon, test_fail, write_daemon_conf,
)

base = SCRATCHDIR / 'mkdirx'
rmtree(base)
base.mkdir()
mod = base / 'mod'
pub = mod / 'public'                     # served dir; only /public/pdir/ excluded
pub.mkdir(parents=True)
(pub / 'served.txt').write_text("served\n")
os.symlink('public', mod / 'blink')
src = base / 'src'
src.mkdir()
(src / 'f0').write_text("NEW\n")

conf = write_daemon_conf(
    [('mod', {'path': str(mod), 'read only': 'no', 'exclude': '/public/pdir/'})])
url = start_test_daemon(conf, 12913)

# --partial-dir resolves (via blink) to the excluded dir public/pdir, which does
# not exist yet -> handle_partial_dir() would mkdir it.
subprocess.run(
    rsync_argv('-a', '--delay-updates', '--partial-dir=/blink/pdir', f'{src}/', f'{url}mod/'),
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

if not (pub / 'pdir').exists():
    test_fail(
        "the daemon refused to create public/pdir via a --partial-dir symlink that "
        "stock rsync (3.2.7) creates.  The daemon exclude filter is name-based "
        "('blink' is not excluded), not a symlink boundary; it must not block this.")
print("daemon exclude is name-based: an operator-path symlink creates a "
      "dir-excluded leaf in a served dir (3.2.7-equivalent)")

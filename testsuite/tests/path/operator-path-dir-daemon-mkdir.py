#!/usr/bin/env python3

import os
import subprocess

from harness.daemon_config import write_daemon_conf
from harness.rsync import SCRATCHDIR, rmtree, rsync_argv, start_test_daemon, test_fail

base = SCRATCHDIR / 'mkdirx'
rmtree(base)
base.mkdir()
mod = base / 'mod'
pub = mod / 'public'
pub.mkdir(parents=True)
(pub / 'served.txt').write_text("served\n")
os.symlink('public', mod / 'blink')
src = base / 'src'
src.mkdir()
(src / 'f0').write_text("NEW\n")

conf = write_daemon_conf(
    [('mod', {'path': str(mod), 'read only': 'no', 'exclude': '/public/pdir/'})])
url = start_test_daemon(conf, 12913)

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

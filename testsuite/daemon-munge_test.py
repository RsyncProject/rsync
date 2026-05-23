#!/usr/bin/env python3
"""Daemon coverage: munge symlinks.

A module with "munge symlinks = yes" stores incoming symlinks with a
/rsyncd-munged/ prefix (so they can't be used to escape the module) and strips
that prefix from outgoing symlinks. Verify both directions on a symlink several
levels deep.
"""

import os
import subprocess

from rsyncfns import (
    FROMDIR, SCRATCHDIR,
    assert_is_symlink, make_tree, makepath, rmtree, rsync_argv,
    start_test_daemon, test_fail, write_daemon_conf,
)

DAEMON_PORT = 12890

src = FROMDIR
deep = os.path.join('d1', 'd2')
rmtree(src)
make_tree(src, depth=3)
os.symlink('f3', src / deep / 'sl')          # deep symlink -> f3

mungedest = SCRATCHDIR / 'mungedest'
pulled = SCRATCHDIR / 'mungepull'
for d in (mungedest, pulled):
    rmtree(d)
makepath(mungedest, pulled)

conf = write_daemon_conf([
    ('munge', {'path': mungedest, 'read only': 'no', 'munge symlinks': 'yes'}),
])
url = start_test_daemon(conf, DAEMON_PORT)

# --- push: the stored symlink is munged with the /rsyncd-munged/ prefix ------
subprocess.run(rsync_argv('-al', f'{src}/', f'{url}munge/'),
               stdout=subprocess.DEVNULL)
stored = mungedest / deep / 'sl'
assert_is_symlink(stored, label='munge stored symlink')
target = os.readlink(stored)
if target != '/rsyncd-munged/f3':
    test_fail(f"munge symlinks stored {target!r}, expected '/rsyncd-munged/f3'")

# --- pull: the prefix is stripped back off on the way out -------------------
subprocess.run(rsync_argv('-al', f'{url}munge/', f'{pulled}/'),
               stdout=subprocess.DEVNULL)
out = pulled / deep / 'sl'
assert_is_symlink(out, target='f3', label='munge stripped on pull')

print("daemon-munge: munge symlinks adds/strips /rsyncd-munged/ at depth")

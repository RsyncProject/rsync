#!/usr/bin/env python3
# Regression: a daemon subdir pull in a symlink-following mode must still follow
# an IN-MODULE symlink whose relative target climbs with ".." to a sibling that
# stays inside the module.
#
# The directory-enumeration confinement must use the same module-root anchor as
# the content open (sender_open_copylinks_confined anchors at module_dir).  A
# daemon chdir's into the requested subdir (do_server_sender, main.c), so
# anchoring the scan at the cwd instead of module_dir would wrongly refuse
# "sub/climb -> ../sibling" -- which resolves to module/sibling, still inside the
# module -- a regression vs the legacy opendir that followed it (while a target
# escaping the module is still refused).  This pulls module subdir "sub/" with
# --copy-dirlinks over an in-module ..-climbing dir symlink and asserts its
# content arrives.

import os

from rsyncfns import (
    SCRATCHDIR, get_rootgid, get_rootuid, get_testuid, makepath, rmtree,
    run_rsync, start_test_daemon, test_fail,
)

DAEMON_PORT = 12948
CONTENT = "in-module sibling\n"

base = SCRATCHDIR / 'daemon-subdir-climb'
rmtree(base)
mod = base / 'mod'
dest = base / 'dest'
makepath(mod / 'sub', mod / 'sibling', dest)
(mod / 'sibling' / 's.txt').write_text(CONTENT)
# In-module dir symlink in the subdir, climbing ".." to a sibling still inside
# the module.  --copy-dirlinks descends it; it must be followed.
os.symlink('../sibling', mod / 'sub' / 'climb')

root = get_testuid() == get_rootuid()
ids = f"uid = {get_rootuid()}\ngid = {get_rootgid()}" if root else ""

conf = base / 'd.conf'
conf.write_text(f"""\
pid file = {base}/rsyncd.pid
use chroot = no
{ids}
log file = {base}/rsyncd.log

[m]
    path = {mod}
    read only = yes
    hosts allow = 127.0.0.1
""")
url = start_test_daemon(conf, DAEMON_PORT)

# Pull the SUBDIR (so the daemon chdir's into mod/sub) with --copy-dirlinks.
run_rsync('-a', '--copy-dirlinks', f'{url}m/sub/', f'{dest}/', check=False)

got = dest / 'climb' / 's.txt'
if not got.is_file() or got.read_text() != CONTENT:
    test_fail(
        "daemon subdir pull with --copy-dirlinks failed to follow an in-module "
        "symlink that climbs '..' to a sibling inside the module "
        "(dest/climb/s.txt missing/wrong): the enumeration must anchor at "
        "module_dir, not the scan cwd.")
print("daemon-subdir-climb-symlink: in-module ..-climbing symlink followed under --copy-dirlinks")

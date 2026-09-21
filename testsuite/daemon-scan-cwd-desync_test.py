#!/usr/bin/env python3
# A daemon directory scan must anchor confinement at the module root by IDENTITY,
# not by climbing ".." up from the cwd by the lexical curr_dir depth.
#
# rsync tracks the path it descended (curr_dir) lexically, but the daemon's
# change_dir follows in-tree directory symlinks.  So when the requested path is an
# in-module directory symlink whose target nets SHALLOWER -- e.g. x/jump -> ../y,
# which from module/x lands at module/y -- the real kernel cwd ends up at module/y
# (depth 1) while curr_dir records module/x/jump (depth 2).  A confinement that
# reaches the module root by climbing ".." up by the lexical depth then climbs too
# far (to module/y's grandparent, ABOVE the module) and resolves the scan there:
# in-module content goes missing, and were a matching path to exist above the
# module it could be enumerated out of the module.  Anchoring at a module-root fd
# pinned by identity resolves the same logical path correctly and stays confined.
#
# Layout (module path = area/mod):
#   mod/y/real.txt          (the in-module content that must be served)
#   mod/x/jump -> ../y       (in-module symlink: mod/x/jump resolves to mod/y)
# Pull mod/x/jump/ with --copy-dirlinks: the daemon change_dir's through jump,
# desyncing curr_dir (mod/x/jump) from the real cwd (mod/y).  A correct, identity-
# anchored scan serves mod/y/real.txt; the buggy lexical climb mis-anchors above
# the module and the file goes missing.
#
# Deterministic (static symlinks).  Requires root for the daemon uid/gid lines;
# runs unprivileged too.

import os

from rsyncfns import (
    SCRATCHDIR, get_rootgid, get_rootuid, get_testuid, makepath, rmtree,
    run_rsync, start_test_daemon, test_fail,
)

DAEMON_PORT = 12950
CONTENT = "served-through-a-cwd-desyncing-symlink\n"

base = SCRATCHDIR / 'scan-desync'
rmtree(base)
area = base / 'area'
mod = area / 'mod'
dest = base / 'dest'
makepath(mod / 'y', mod / 'x', dest)
(mod / 'y' / 'real.txt').write_text(CONTENT)
# In-module symlink whose target nets shallower: mod/x/jump -> ../y == mod/y.
os.symlink('../y', mod / 'x' / 'jump')

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

# Pull the in-module net-shallower symlinked path; the daemon descends through it.
run_rsync('-a', '--copy-dirlinks', f'{url}m/x/jump/', f'{dest}/', check=False)

got = dest / 'real.txt'
if not got.is_file() or got.read_text() != CONTENT:
    test_fail(
        "daemon scan failed to serve in-module content reached through a "
        "cwd-desyncing in-module symlink (mod/x/jump -> ../y): dest/real.txt "
        "missing/wrong.  The scan must anchor at the module root by identity "
        "(module_dirfd), not by climbing the lexical curr_dir depth -- the latter "
        "over-climbs above the module when an in-module symlink nets shallower.")
print("daemon-scan-cwd-desync-escape: in-module content served correctly through "
      "a cwd-desyncing symlink (scan stayed anchored at the module root)")

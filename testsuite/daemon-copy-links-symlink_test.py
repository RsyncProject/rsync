#!/usr/bin/env python3
# A daemon must follow an IN-TREE symlink in its served module when the client
# asks to dereference it (-L copy-links / -k copy-dirlinks) -- the symlink is
# inside the operator's own served tree, which 3.4.x follows.
#
# A/B + fuzzer-discovered 3.5 regression (abdiff --fuzz: 16 candidates, one root
# cause): an early per-component O_NOFOLLOW resolver made the daemon SENDER refuse
# to follow the in-tree dir-symlink that -L/-k must dereference (exit 23, files
# lost; the -L pull case could even exit 0 while SILENTLY dropping the symlinked
# content).  The secure resolver now follows in-tree directory symlinks on every
# platform, so -L/-k over the daemon transport works uniformly; 3.4.x is unaffected.
#
# Same root cause as the local -K (keep-dirlinks-symlinked-dest) and -aR
# (relative-symlinked-parent) regressions, but a BROADER surface: it adds -L and
# -k, over the DAEMON transport (the local -L/-k paths are unaffected).  No root;
# uses the standard test-daemon seam (no real TCP in the default mode).

import os

from rsyncfns import (SCRATCHDIR, assert_same, get_rootgid, get_rootuid,
                      get_testuid, makepath, rmtree, run_rsync,
                      start_test_daemon, test_fail)

DAEMON_PORT = 12898
base = SCRATCHDIR / 'daemon_copylinks'
rmtree(base)
mod = base / 'mod'
makepath(mod / 'dir' / 'sub', base / 'dest')
(mod / 'dir' / 'a.txt').write_text('payload a\n')
(mod / 'dir' / 'sub' / 'b.txt').write_text('payload b\n')
os.symlink('dir', mod / 'dir_link')          # in-tree dir-symlink within the module

# When the suite runs as root (e.g. the CI fleet's sudo pass) a use-chroot=no
# daemon otherwise drops to nobody and can't read the root-owned module, so the
# sender fails with EACCES on every file -- nothing to do with the symlink.
# Pin uid/gid to root in that case, as build_rsyncd_conf() does.  Non-root
# cannot set uid/gid (and doesn't need to: it owns the module).
root = get_testuid() == get_rootuid()
ids = f"uid = {get_rootuid()}\ngid = {get_rootgid()}" if root else ""

conf = base / 'copylinks.conf'
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

# -L (copy-links): dir_link must arrive as a real directory with its content.
# check=False: the fallback regression manifests either as exit 23 or as a
# silent exit-0 partial copy -- assert on the resulting tree either way.
run_rsync('-aL', f'{url}m/', f'{base}/dest/', check=False)

for rel in ('dir_link/a.txt', 'dir_link/sub/b.txt'):
    if not (base / 'dest' / rel).is_file():
        test_fail(f"daemon pull -aL dropped in-tree symlinked content: dest/{rel} missing")
assert_same(mod / 'dir' / 'a.txt', base / 'dest' / 'dir_link' / 'a.txt', label='content')

print("daemon-copy-links-symlink: daemon -aL follows an in-tree dir-symlink in the module")

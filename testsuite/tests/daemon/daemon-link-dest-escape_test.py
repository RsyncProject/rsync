#!/usr/bin/env python3
"""A daemon must confine a client's --link-dest/--compare-dest/--copy-dest basis
dir to the served module (Leonid Bugaev May-2026 re-audit, KI-48 / CVE-53795
surface; defense-in-depth guard).

basis_link_stat() joins the peer-supplied basis dir with each fname and stats it.
A client sending --link-dest=../<sibling> (or an absolute path) must NOT make the
daemon stat files outside the module -- that would be an existence/size/mode
oracle for out-of-tree paths, even though CVE-53795 already refuses the
cross-boundary hardlink itself.

The daemon already clamps such a path to the module root (longstanding sanitize
behavior, preserved through this branch's d33e599c path-confinement rework -- the
audit's "runtime-confirmed" KI-48 was a *local* receiver, where --link-dest=..
is the operator's own intended choice, not a boundary crossing).  This test locks
that confinement in: it pushes with --link-dest=../<sibling> where <sibling> is a
real dir next to the module root; the daemon reports "link-dest arg does not
exist" (to the client, via -v) only when it could NOT reach the dir, so reaching
the real out-of-tree dir (no warning) would be a regression/leak.
"""

import os
import subprocess

from rsyncfns import (
    SCRATCHDIR, makepath, rmtree, rsync_argv, start_test_daemon, test_fail,
    write_daemon_conf,
)

DAEMON_PORT = 12894

# Layout:  base/mod/        <- the served module (read only=no, push target)
#          base/sibling/    <- OUTSIDE the module; the client must not reach it
base = SCRATCHDIR / 'kid48'
rmtree(base)
mod = base / 'mod'
sibling = base / 'sibling'
makepath(mod)
makepath(sibling)
(sibling / 'probe').write_text('out-of-tree basis content\n')

# Client source to push.
src = base / 'src'
makepath(src)
(src / 'probe').write_text('client content\n')

conf = write_daemon_conf([
    ('mod', {'path': str(mod), 'read only': 'no'}),
])
url = start_test_daemon(conf, DAEMON_PORT)

# Push with --link-dest=../sibling (a real dir just outside the module root).
proc = subprocess.run(
    rsync_argv('-rv', '--link-dest=../sibling', f'{src}/', f'{url}mod/'),
    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
out = proc.stdout or ''

# The daemon (receiver) runs check_alt_basis_dirs() / basis_link_stat() against
# the basis dir relative to the module root.  Confined: "../sibling" is clamped
# to "sibling" inside the module, which does not exist -> a "does not exist"
# warning.  Leaked: the daemon stats base/sibling (the real out-of-tree dir),
# so there is NO such warning.
reached_outside = 'does not exist' not in out and 'is not a dir' not in out

if reached_outside:
    test_fail(
        "daemon reached outside the module via --link-dest=../sibling: the "
        "out-of-tree directory was stat'd (no 'arg does not exist' warning), "
        "leaking the existence of paths outside the module.\n--- client saw ---\n"
        + out)

print("daemon-link-dest-escape: --link-dest=../sibling was confined to the "
      "module (out-of-tree basis dir not reachable)")

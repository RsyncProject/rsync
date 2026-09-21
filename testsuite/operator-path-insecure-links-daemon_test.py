#!/usr/bin/env python3
# A daemon must NEVER honor a peer-forwarded --insecure-links: a client cannot be
# allowed to switch off the daemon's symlink confinement (otherwise a malicious
# client just always sends it).  rsync no longer forwards --insecure-links to the
# server (it is a local-only opt-out), but a client can still inject it explicitly
# with -M/--remote-option, so this guards that every daemon-side confinement gate
# ignores it -- the opt-out predicate is am_daemon ? lp_insecure_links(module_id)
# : insecure_links, which never reads the peer-controllable insecure_links on a
# daemon.  (A daemon also hard-refuses an explicitly-injected --insecure-links via
# the refused-options path; see operator-path-insecure-links-refused.)
#
# Exercised via the module-root chdir (change_dir): the daemon serves a module
# whose configured `path =` is a foreign-owned symlink to an escape dir.  The
# daemon's chdir must refuse it EVEN when the client forwards -M--insecure-links.
# (change_dir's daemon branch is already always-confined; this locks that in and
# guards the dir-sink/file-open fixes that add the opt-out gate.)
#
# Requires root for the cross-uid plant.

import os
import subprocess

from rsyncfns import (
    SCRATCHDIR, find_attacker_uid, rmtree, rsync_argv, start_test_daemon,
    test_fail, test_skipped, write_daemon_conf,
)

DAEMON_PORT = 12901
CANARY = "audit_daemon_optout_canary"

if os.geteuid() != 0:
    test_skipped("requires root to plant a symlink owned by a non-self uid")
ATT = find_attacker_uid()
if ATT is None:
    test_skipped("no untrusted-uid user available for cross-uid plant")

base = SCRATCHDIR / 'daemonoptout'
rmtree(base)
base.mkdir()
escape_target = base / 'escape_target'
escape_target.mkdir()
(escape_target / CANARY).write_text("must-not-be-served\n")

plant = base / 'plant'                       # the module-configured `path =`
os.symlink(escape_target, plant)
os.lchown(plant, ATT, ATT)                   # foreign-owned (attacker) symlink

conf = write_daemon_conf([('mod', {'path': plant, 'read only': 'yes'})])
url = start_test_daemon(conf, DAEMON_PORT)

# The client forwards --insecure-links to the daemon (-M / --remote-option).
proc = subprocess.run(
    rsync_argv('-M--insecure-links', f'{url}mod/'),
    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

if CANARY in proc.stdout:
    test_fail(
        "the daemon honored a peer-forwarded --insecure-links and disabled its "
        f"module-chdir confinement: the foreign-owned symlink {plant} -> "
        f"{escape_target} was followed (canary {CANARY!r} in the listing). A "
        "daemon must never let a client switch off its symlink confinement; the "
        "opt-out must be gated on !am_daemon, not bare insecure_links.")
print("daemon ignores peer-forwarded --insecure-links: confinement held")

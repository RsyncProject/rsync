#!/usr/bin/env python3
# Daemon module-root chdir under `use chroot = no` must not follow planted
# symlinks at attacker-controlled path components.
#
# Threat: when a daemon is
# configured with `use chroot = no`, it calls change_dir(module_chdir)
# where module_chdir is the absolute path from rsyncd.conf `path =`.
# change_dir's absolute-path branch (util1.c:1193) is plain chdir() with
# NO symlink confinement. If the admin's configured path includes a
# component the attacker controls (e.g. `path = /home/$user/share`, or any
# /srv-style path under a non-root-owned parent), an attacker-planted
# symlink redirects the daemon's CWD out of the configured module before
# any transfer begins.
#
# This is the daemon-side analog of the symlink-race-dest XFAIL (the
# non-daemon receiver chdir); the daemon path has a clear fix here without
# the chdir-of-named-dest scope concern.
#
# Leak signal: configure the daemon with `path = $plant` where $plant is
# a planted symlink to an "escape" directory containing a unique-named
# canary file that the legitimate module doesn't have. List the module
# from a client; if the canary appears in the listing, the daemon's chdir
# followed the planted symlink and is serving files from the escape
# target. With the fix the chdir is refused and the daemon errors on
# connection.
#
# Requires root for the cross-uid plant; skips otherwise.

import os
import pwd
import subprocess

from rsyncfns import (
    SCRATCHDIR, rmtree, rsync_argv, start_test_daemon, test_fail,
    test_skipped, write_daemon_conf,
)

DAEMON_PORT = 12900
CANARY = "audit_escape_canary"


if os.geteuid() != 0:
    test_skipped("requires root to plant a symlink owned by a non-self uid "
                 "(the attacker simulation)")

NOBODY_UID = None
for name in ('nobody', 'nfsnobody', 'daemon'):
    try:
        u = pwd.getpwnam(name).pw_uid
        if u != 0 and u != os.geteuid():
            NOBODY_UID = u
            break
    except KeyError:
        continue
if NOBODY_UID is None:
    test_skipped("no untrusted-uid user available for cross-uid plant")


base = SCRATCHDIR / 'modchdir'
rmtree(base)
base.mkdir()

# The "escape" directory the attacker wants the daemon to serve from.
# Owned by us, with a canary file the legitimate module doesn't have.
escape_target = base / 'escape_target'
escape_target.mkdir()
(escape_target / CANARY).write_text("you-should-not-see-this\n")

# The planted symlink: appears at the daemon-configured 'path =' and
# redirects to the escape target. The symlink itself is owned by NOBODY_UID
# (the attacker), so the fix's owned-by-trusted-uid path walk refuses it.
plant = base / 'plant'
os.symlink(escape_target, plant)
os.lchown(plant, NOBODY_UID, NOBODY_UID)

# Daemon config: `path = $plant`. With `use chroot = no` (the default in
# write_daemon_conf) the daemon will chdir into the resolved path before
# serving the module.
conf = write_daemon_conf(
    [('mod', {'path': plant, 'read only': 'yes'})],
)
url = start_test_daemon(conf, DAEMON_PORT)

# Client lists the module's contents (rsync $url/mod/). Without the fix the
# daemon's chdir followed the planted symlink and is serving from
# escape_target -> canary appears in the listing.
proc = subprocess.run(
    rsync_argv(f'{url}mod/'),
    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

if CANARY in proc.stdout:
    test_fail(
        f"daemon module-root chdir followed a planted parent-component "
        f"symlink (owned by uid {NOBODY_UID}): {plant} -> {escape_target} "
        f"was traversed by the plain chdir() in change_dir(), and the daemon "
        f"is now serving files from the escape target (canary "
        f"{CANARY!r} appeared in the module listing). Fix: per-component "
        "owned-by-trusted-uid path walk in change_dir's absolute-path branch "
        "under am_daemon && !am_chrooted.")

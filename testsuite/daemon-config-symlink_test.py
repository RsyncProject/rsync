#!/usr/bin/env python3
# Daemon ancillary opens (motd / early-input / lock / --config rsyncd.conf)
# must not follow planted symlinks at attacker-controlled path components.
#
# Threat: admin-configured paths in
# rsyncd.conf or on the daemon's command line are operator-supplied, but the
# /tmp-class trust model applies (admin reasonably picks /tmp or another
# shared-parent path for one-off use; the attacker plants in that parent).
# Each of these opens is plain fopen()/open() without O_NOFOLLOW or path
# walk:
#   clientserver.c:160   fopen(motd, "r")
#   clientserver.c:269   fopen(early_input_file, "rb")
#   connection.c:33      open(lock_file, O_RDWR|O_CREAT, 0600)
#   params.c:583         fopen(rsyncd.conf, "r")
#
# This test exercises the MOTD case as the representative -- the leak signal
# is direct: when the planted symlink is followed, its target's bytes are
# read and sent to the connecting client as the message of the day. With the
# fix the open is refused; the daemon either sends no motd or errors, but
# the leaked content does not reach the client. The other three call sites
# (early-input, lock, --config) are covered by the SAME path-walk helper
# applied uniformly; their behaviour is not separately exercised here.
#
# Requires root for the cross-uid plant; skips otherwise.

import os
import pwd
import subprocess

from rsyncfns import (
    FROMDIR, SCRATCHDIR, make_tree, rmtree, rsync_argv, start_test_daemon,
    test_fail, test_skipped, write_daemon_conf,
)

DAEMON_PORT = 12901
MARKER = "AUDIT_MOTD_LEAK_MARKER_DO_NOT_DISCLOSE"


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


# Daemon setup
src = FROMDIR
rmtree(src)
make_tree(src, depth=1)

base = SCRATCHDIR / 'daemoncfg'
rmtree(base)
base.mkdir()
plants = base / 'plants'
plants.mkdir()
modpath = base / 'mod'
modpath.mkdir()

# The victim file. The attacker plants a symlink at the motd path pointing
# here; the file's content is the unique marker. If the marker reaches the
# connecting client, the leak fired.
victim = plants / 'motd_victim'
victim.write_text(MARKER + "\n")
os.chmod(victim, 0o600)

motd_plant = plants / 'motd_plant'
os.symlink(victim, motd_plant)
os.lchown(motd_plant, NOBODY_UID, NOBODY_UID)

# Lock-file plant.  The daemon uses 'lock file =' for max-connections gating
# (connection.c:33: open(fname, O_RDWR|O_CREAT, 0600)) -- only when
# max_connections > 0, so we set max_connections=5 below to force the open.
# A planted symlink at the lock-file path redirects the daemon's
# O_CREAT-open to an attacker-chosen path.  We plant a symlink pointing at
# a victim-directory entry inside our scratch tree; if the daemon followed
# it, the file gets created at the planted target on first client connect.
lock_victim_dir = base / 'lock_victim'
lock_victim_dir.mkdir()
lock_plant = plants / 'lock_plant'
os.symlink(lock_victim_dir / 'leaked_lockfile', lock_plant)
os.lchown(lock_plant, NOBODY_UID, NOBODY_UID)

conf = write_daemon_conf(
    [('mod', {'path': modpath, 'read only': 'yes',
              'max connections': '5'})],
    globals={'motd file': str(motd_plant),
             'lock file': str(lock_plant)},
)
url = start_test_daemon(conf, DAEMON_PORT)

# Connect with the rsync client; the daemon will (or won't) send the motd
# content to the client during connection setup. Listing the module triggers
# motd, and -- since max_connections > 0 on `mod` -- ALSO triggers the lock-
# file open via claim_connection().
proc = subprocess.run(
    rsync_argv(url + 'mod/'),
    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

if MARKER in proc.stdout or MARKER in proc.stderr:
    test_fail(
        f"daemon followed a planted motd symlink (owned by uid {NOBODY_UID}): "
        f"the marker {MARKER!r} from {victim} was read via the symlink and "
        "sent to the connecting client as the message of the day. Fix: refuse "
        "planted symlinks at the daemon motd open (and analogously for "
        "early-input / lock / --config).")

# Did the daemon's claim_connection() follow the planted lock-file symlink
# and create a file at the attacker-chosen location?
leaked_lockfile = lock_victim_dir / 'leaked_lockfile'
if leaked_lockfile.exists():
    test_fail(
        f"daemon followed a planted lock-file symlink (owned by uid "
        f"{NOBODY_UID}): {lock_plant} -> {leaked_lockfile} was traversed by "
        f"claim_connection()'s open(O_CREAT) at connection.c:33, creating a "
        f"root-owned file at the attacker-chosen path. Fix: refuse planted "
        "symlinks at the daemon lock-file open.")

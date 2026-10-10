#!/usr/bin/env python3

import os
import pwd
import subprocess

from harness.rsync import (
    SCRATCHDIR, rmtree, rsync_argv, start_test_daemon, test_fail,
    test_skipped, write_daemon_conf,
)

DAEMON_PORT = 12900
CANARY = "audit_escape_canary"

if os.geteuid() != 0:
    test_skipped("requires root to plant a symlink owned by a non-self uid "
                 "(the attacker simulation)", capability='cross_uid')

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
    test_skipped("no untrusted-uid user available for cross-uid plant", capability='cross_uid')

base = SCRATCHDIR / 'modchdir'
rmtree(base)
base.mkdir()

escape_target = base / 'escape_target'
escape_target.mkdir()
(escape_target / CANARY).write_text("you-should-not-see-this\n")

plant = base / 'plant'
os.symlink(escape_target, plant)
os.lchown(plant, NOBODY_UID, NOBODY_UID)

conf = write_daemon_conf(
    [('mod', {'path': plant, 'read only': 'yes'})],
)
url = start_test_daemon(conf, DAEMON_PORT)

proc = subprocess.run(
    rsync_argv(f'{url}mod/'),
    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

if CANARY in proc.stdout:
    test_fail(f'daemon served {CANARY!r} through untrusted uid {NOBODY_UID} '
              f'module symlink: {plant} -> {escape_target}')

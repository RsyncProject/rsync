#!/usr/bin/env python3

import os
import subprocess

from harness.mutation import find_attacker_uid
from harness.daemon_config import write_daemon_conf
from harness.rsync import SCRATCHDIR, rmtree, rsync_argv, start_test_daemon, test_fail, test_skipped

DAEMON_PORT = 12901
CANARY = "audit_daemon_optout_canary"

if os.geteuid() != 0:
    test_skipped("requires root to plant a symlink owned by a non-self uid", capability='cross_uid')
ATT = find_attacker_uid()
if ATT is None:
    test_skipped("no untrusted-uid user available for cross-uid plant", capability='cross_uid')

base = SCRATCHDIR / 'daemonoptout'
rmtree(base)
base.mkdir()
escape_target = base / 'escape_target'
escape_target.mkdir()
(escape_target / CANARY).write_text("must-not-be-served\n")

plant = base / 'plant'
os.symlink(escape_target, plant)
os.lchown(plant, ATT, ATT)

conf = write_daemon_conf([('mod', {'path': plant, 'read only': 'yes'})])
url = start_test_daemon(conf, DAEMON_PORT)

proc = subprocess.run(
    rsync_argv('-M--insecure-links', f'{url}mod/'),
    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

if CANARY in proc.stdout:
    test_fail(
        f'daemon honoured peer --insecure-links and served {CANARY!r} through '
        f'{plant} -> {escape_target}')
print("daemon ignores peer-forwarded --insecure-links: confinement held")

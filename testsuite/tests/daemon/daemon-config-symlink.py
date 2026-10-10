#!/usr/bin/env python3

import os
import pwd
import subprocess

from harness.rsync import (
    FROMDIR, SCRATCHDIR, make_tree, rmtree, rsync_argv, start_test_daemon,
    test_fail, test_skipped, write_daemon_conf,
)

DAEMON_PORT = 12901
MARKER = "AUDIT_MOTD_LEAK_MARKER_DO_NOT_DISCLOSE"

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

victim = plants / 'motd_victim'
victim.write_text(MARKER + "\n")
os.chmod(victim, 0o600)

motd_plant = plants / 'motd_plant'
os.symlink(victim, motd_plant)
os.lchown(motd_plant, NOBODY_UID, NOBODY_UID)

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

proc = subprocess.run(
    rsync_argv(url + 'mod/'),
    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

if MARKER in proc.stdout or MARKER in proc.stderr:
    test_fail(f'daemon read motd through untrusted uid {NOBODY_UID} symlink: '
              f'{motd_plant} -> {victim}')

leaked_lockfile = lock_victim_dir / 'leaked_lockfile'
if leaked_lockfile.exists():
    test_fail(f'daemon created a lock file through untrusted uid {NOBODY_UID} '
              f'symlink: {lock_plant} -> {leaked_lockfile}')

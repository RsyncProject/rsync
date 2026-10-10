#!/usr/bin/env python3

import os
import subprocess

from harness.mutation import find_attacker_uid
from harness.rsync import (
    SCRATCHDIR, rmtree, rsync_argv, rsync_supports, start_test_daemon, test_fail, test_skipped,
    write_daemon_conf,
)

DAEMON_PORT = 12906
MARKER = "OUT-OF-MODULE-SECRET\n"

if os.geteuid() != 0:
    test_skipped("requires root to plant a foreign-owned symlink and serve as root",
                 capability='cross_uid')
ATT = find_attacker_uid()
if ATT is None:
    test_skipped("no untrusted-uid user available for cross-uid plant", capability='cross_uid')
if not rsync_supports('--copy-dirlinks'):
    test_skipped("rsync lacks --copy-dirlinks")

base = SCRATCHDIR / 'insecure-admin'
rmtree(base)
mod = base / 'mod'
outside = base / 'outside'
mod.mkdir(parents=True)
outside.mkdir(parents=True)

(mod / 'intree.txt').write_text("in-module\n")
(outside / 'secret.txt').write_text(MARKER)
os.symlink('../outside', mod / 'sub')
os.lchown(mod / 'sub', ATT, ATT)

conf = write_daemon_conf([
    ('mod_secure', {'path': str(mod), 'use chroot': 'no', 'read only': 'yes',
                    'uid': '0', 'gid': '0'}),
    ('mod_insecure', {'path': str(mod), 'use chroot': 'no', 'read only': 'yes',
                      'uid': '0', 'gid': '0', 'insecure links': 'yes'}),
])
url = start_test_daemon(conf, DAEMON_PORT)

def pull(modname):
    out = base / f'dest_{modname}'
    rmtree(out)
    out.mkdir()
    subprocess.run(
        rsync_argv('-r', '--copy-dirlinks', f'{url}{modname}/', f'{out}/'),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    served = out / 'sub' / 'secret.txt'
    return served.is_file() and served.read_text() == MARKER

if pull('mod_secure'):
    test_fail("mod_secure served the out-of-module file through the planted "
              "directory symlink: the daemon followed it without `insecure "
              "links = yes` -- confinement failed.")

if not pull('mod_insecure'):
    test_fail("mod_insecure did NOT serve the out-of-module file even with "
              "`insecure links = yes`: the per-module admin opt-out was not "
              "honoured by the sender enumeration/content gates.")

print("insecure links = yes: refused by default, honoured as an admin opt-out")

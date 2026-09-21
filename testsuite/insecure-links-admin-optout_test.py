#!/usr/bin/env python3
# Admin per-module opt-out: `insecure links = yes` (rsyncd.conf).
#
# A CLIENT can never switch off a daemon's symlink confinement -- that is the
# operator-path-insecure-links-{daemon,refused} guard.  But an ADMIN may, for an
# isolated/trusted module, restore the legacy follow-any-symlink behaviour with
# `insecure links = yes` (the daemon analogue of the local --insecure-links).
# This re-opens the symlink-escape vectors for that module ON PURPOSE, so the
# test asserts both halves of the rule using two modules over one daemon, both
# rooted at the same tree with the same planted escape:
#
#   - mod_secure   (default)            -> the escape is REFUSED (confined)
#   - mod_insecure (insecure links=yes) -> the escape is SERVED  (opt-out honored)
#
# The escape is a directory symlink inside the module pointing OUT of the module;
# a --copy-dirlinks pull enumerates through it (flist secure_opendir) and reads
# the out-of-tree file (sender content open).  Both gates consult the per-module
# opt-out, so the out-of-module file arrives only for the insecure module.
#
# Requires root: the planted symlink is foreign-owned, matching the threat model
# (an attacker-owned in-module symlink), and the module is served as root.

import os
import subprocess

from rsyncfns import (
    SCRATCHDIR, find_attacker_uid, rmtree, rsync_argv, rsync_supports,
    start_test_daemon, test_fail, test_skipped, write_daemon_conf,
)

DAEMON_PORT = 12906
MARKER = "OUT-OF-MODULE-SECRET\n"

if os.geteuid() != 0:
    test_skipped("requires root to plant a foreign-owned symlink and serve as root")
ATT = find_attacker_uid()
if ATT is None:
    test_skipped("no untrusted-uid user available for cross-uid plant")
if not rsync_supports('--copy-dirlinks'):
    test_skipped("rsync lacks --copy-dirlinks")

base = SCRATCHDIR / 'insecure-admin'
rmtree(base)
mod = base / 'mod'
outside = base / 'outside'
mod.mkdir(parents=True)
outside.mkdir(parents=True)

# In-module content (always servable) plus the escaping directory symlink.
(mod / 'intree.txt').write_text("in-module\n")
(outside / 'secret.txt').write_text(MARKER)
os.symlink('../outside', mod / 'sub')          # dir symlink -> out of module
os.lchown(mod / 'sub', ATT, ATT)               # foreign-owned (attacker) plant

# Serve as root (uid/gid 0) so the daemon euid differs from the attacker uid.
# Use numeric 0, not the name "root": the root *group* is "wheel" on the BSDs
# and macOS, so `gid = root` is rejected there ("Invalid gid root").
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


# Default module: the foreign-owned escaping symlink must be refused.
if pull('mod_secure'):
    test_fail("mod_secure served the out-of-module file through the planted "
              "directory symlink: the daemon followed it without `insecure "
              "links = yes` -- confinement failed.")

# Opt-out module: the admin asked for legacy following, so it must be honored.
if not pull('mod_insecure'):
    test_fail("mod_insecure did NOT serve the out-of-module file even with "
              "`insecure links = yes`: the per-module admin opt-out was not "
              "honored by the sender enumeration/content gates.")

print("insecure links = yes: refused by default, honored as an admin opt-out")

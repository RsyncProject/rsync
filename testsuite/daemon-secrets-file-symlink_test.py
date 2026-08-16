#!/usr/bin/env python3
# Daemon 'secrets file = PATH' must not follow planted symlinks at attacker-
# controlled path components.
#
# Companion to password-file-symlink (which covers the CLIENT --password-file);
# this covers the DAEMON-side 'secrets file' open in authenticate.c. Same path-
# walk helper (open_no_attacker_symlinks), exercised directly here because
# the daemon secrets read is a distinct call site with its own leak signal.
#
# Threat: the daemon opens 'secrets file' to authenticate a client. A symlink
# planted at that path (or a parent component) by another uid redirects the read
# to an attacker-chosen file. The strict-modes fstat() runs on the *target*
# inode, so a symlink to e.g. /etc/shadow (root-owned, not other-readable) would
# pass the owner/mode check and the daemon would auth against the target's
# contents.
#
# Leak signal: the planted symlink points at a victim secrets file (root:0600)
# whose contents ARE a known user:password. If the daemon follows it, a client
# authenticating with that password SUCCEEDS -> leak observed. With the fix the
# planted symlink is refused ("no secrets file"), so auth fails.
#
# Both leaf and parent-component variants are exercised (the parent case is the
# /tmp/somedir/ class that a leaf-only O_NOFOLLOW does not defend).
#
# Requires root (to lchown the planted symlink to a non-self uid -- the attacker
# simulation); skips otherwise.

import os
import pwd
import subprocess

from rsyncfns import (
    FROMDIR, SCRATCHDIR, make_tree, makepath, rmtree, rsync_argv,
    start_test_daemon, test_fail, test_skipped, write_daemon_conf,
)

DAEMON_PORT = 12903
USER = 'tuser'
CORRECT_PASSWORD = 'correctpass'


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

# Prevent rsync's interactive getpass() fallback if no password is read.
os.environ['RSYNC_PASSWORD'] = 'env-fallback-wrong'

src = FROMDIR
rmtree(src)
make_tree(src, depth=1)

base = SCRATCHDIR / 'secretsplant'
rmtree(base)
base.mkdir()

# The victim secrets file: root-owned, 0600, contents == a valid credential.
# A daemon that follows a planted symlink to this file authenticates the client.
victim = base / 'victim_secrets'
victim.write_text(f'{USER}:{CORRECT_PASSWORD}\n')
os.chmod(victim, 0o600)
os.chown(victim, 0, 0)

# Leaf plant: a symlink AT the secrets-file path, owned by the attacker uid.
leaf_plant = base / 'secrets_leaf'
os.symlink(victim, leaf_plant)
os.lchown(leaf_plant, NOBODY_UID, NOBODY_UID)

# Parent-component plant: secrets file = <parent_link>/victim_in_target, where
# parent_link is an attacker-owned symlink to a real directory holding the
# victim secrets. A leaf-only O_NOFOLLOW does not defend this; only the per-
# component owner-checked walk does.
parent_real = base / 'parent_real'
parent_real.mkdir()
victim_in_target = parent_real / 'victim_in_target'
victim_in_target.write_text(f'{USER}:{CORRECT_PASSWORD}\n')
os.chmod(victim_in_target, 0o600)
os.chown(victim_in_target, 0, 0)
parent_plant = base / 'parent_link'
os.symlink(parent_real, parent_plant)
os.lchown(parent_plant, NOBODY_UID, NOBODY_UID)

authreal = base / 'authreal'
authleaf = base / 'authleaf'
authparent = base / 'authparent'
makepath(authreal, authleaf, authparent)

conf = write_daemon_conf([
    # Sanity module: a real (non-symlinked) root:0600 secrets file authenticates.
    ('authreal', {'path': authreal, 'read only': 'no',
                  'auth users': USER, 'secrets file': victim}),
    # Leak modules: the secrets file is reached through an attacker-owned symlink.
    ('authleaf', {'path': authleaf, 'read only': 'no',
                  'auth users': USER, 'secrets file': leaf_plant}),
    ('authparent', {'path': authparent, 'read only': 'no',
                    'auth users': USER,
                    'secrets file': parent_plant / 'victim_in_target'}),
])
url = start_test_daemon(conf, DAEMON_PORT)
userurl = url.replace('rsync://', f'rsync://{USER}@', 1)

pwfile = SCRATCHDIR / 'pw.ok'
pwfile.write_text(CORRECT_PASSWORD + '\n')
pwfile.chmod(0o600)


def push(module, dest):
    rmtree(dest)
    makepath(dest)
    return subprocess.run(
        rsync_argv('-a', f'--password-file={pwfile}',
                   f'{src}/', f'{userurl}{module}/'),
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)


# Sanity: the real secrets file authenticates (demonstrates the machinery;
# success against a planted symlink is then the leak signal).
sanity = push('authreal', authreal)
if sanity.returncode != 0:
    test_fail(
        "sanity check failed: a non-symlinked root:0600 secrets file with the "
        f"correct credential could not authenticate. stderr: {sanity.stderr!r}")

# Leaf plant: success means the daemon followed the attacker-owned symlink.
leaf = push('authleaf', authleaf)
if leaf.returncode == 0:
    test_fail(
        f"daemon 'secrets file' followed a planted LEAF symlink (owned by uid "
        f"{NOBODY_UID}): {leaf_plant} -> {victim} was read as the secrets DB and "
        f"the client authenticated -> transfer succeeded. Fix: refuse planted "
        f"symlinks at the daemon secrets-file open.")

# Parent-component plant: success means the daemon traversed the attacker-owned
# parent symlink.
parent = push('authparent', authparent)
if parent.returncode == 0:
    test_fail(
        f"daemon 'secrets file' followed a planted PARENT-component symlink "
        f"(owned by uid {NOBODY_UID}): {parent_plant} -> {parent_real} was "
        f"traversed and victim_in_target read as the secrets DB; the client "
        f"authenticated -> transfer succeeded. This is the /tmp/somedir/-class "
        f"parent-flip case. Fix: per-component path walk refusing parent "
        f"symlinks owned by untrusted uids.")

print("daemon-secrets-file-symlink: leaf + parent plants refused")

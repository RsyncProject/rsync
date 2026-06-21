#!/usr/bin/env python3
# --password-file=PATH must not follow planted symlinks at attacker-
# controlled path components.
#
# Threat: a privileged client runs
# rsync with --password-file=PATH where PATH lives in an attacker-
# influenceable directory. A planted symlink redirects the read to an
# arbitrary file (e.g. /etc/shadow); rsync reads the symlinked content and
# sends it as the daemon auth response. A malicious daemon (or simply
# error/timing channels) observes the leaked content.
#
# The leak signal in this test is direct: the daemon is configured with a
# known correct password, and the planted symlink points at a victim file
# whose content IS that correct password. With the vulnerability rsync
# reads the victim, sends the correct password, and the daemon accepts ->
# transfer SUCCEEDS (returncode 0) -> LEAK observed. With the fix the
# planted symlink is refused, rsync sends no password, and auth fails ->
# transfer fails (returncode != 0).
#
# Both leaf and parent-component variants exercised (the parent case is
# the /tmp/somedir/ class that plain O_NOFOLLOW does NOT defend).
#
# Requires root (to lchown the planted symlink to a non-self uid -- the
# attacker simulation); skips otherwise.

import os
import pwd
import subprocess

from rsyncfns import (
    FROMDIR, SCRATCHDIR, make_tree, makepath, rmtree, rsync_argv,
    start_test_daemon, test_fail, test_skipped, write_daemon_conf,
)

DAEMON_PORT = 12902
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

# Match daemon-auth_test.py: prevent rsync's interactive getpass() fallback
# if no password is read.
os.environ['RSYNC_PASSWORD'] = 'env-fallback-wrong'

# Daemon setup
src = FROMDIR
rmtree(src)
make_tree(src, depth=1)

authdir = SCRATCHDIR / 'authdest'
secrets = SCRATCHDIR / 'rsyncd.secrets'
secrets.write_text(f'{USER}:{CORRECT_PASSWORD}\n')
secrets.chmod(0o600)

conf = write_daemon_conf([
    ('auth', {'path': authdir, 'read only': 'no',
              'auth users': USER, 'secrets file': secrets}),
])
url = start_test_daemon(conf, DAEMON_PORT)
userurl = url.replace('rsync://', f'rsync://{USER}@', 1)

# Victim file the attacker wants rsync to read. Owned by us, mode 0600.
# Crucially: content == the correct daemon password, so a SUCCESSFUL
# transfer is the leak signal.
victim = SCRATCHDIR / 'victim_password'
victim.write_text(CORRECT_PASSWORD + '\n')
victim.chmod(0o600)


def push_with_pwfile(pwfile_path):
    """Run an rsync push via the daemon with --password-file=pwfile_path."""
    rmtree(authdir)
    makepath(authdir)
    return subprocess.run(
        rsync_argv('-a', f'--password-file={pwfile_path}',
                   f'{src}/', f'{userurl}auth/'),
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)


# ---------------------------------------------------------------------------
# Sanity: a real password file with the correct password authenticates.
# Demonstrates the test machinery: success == leak when we later use a planted
# symlink that resolves to this same content.
# ---------------------------------------------------------------------------
real_pwfile = SCRATCHDIR / 'real_pwfile'
real_pwfile.write_text(CORRECT_PASSWORD + '\n')
real_pwfile.chmod(0o600)

sanity = push_with_pwfile(real_pwfile)
if sanity.returncode != 0:
    test_fail(
        "sanity check failed: a non-symlinked password file with the correct "
        f"password could not authenticate. stderr: {sanity.stderr!r}")


# ---------------------------------------------------------------------------
# LEAF plant.
# ---------------------------------------------------------------------------
plants = SCRATCHDIR / 'plants'
rmtree(plants); plants.mkdir()
leaf_plant = plants / 'pwfile_leaf'
os.symlink(victim, leaf_plant)
os.lchown(leaf_plant, NOBODY_UID, NOBODY_UID)

leaf = push_with_pwfile(leaf_plant)
if leaf.returncode == 0:
    test_fail(
        f"--password-file followed a planted LEAF symlink (owned by uid "
        f"{NOBODY_UID}): rsync read {victim} via the symlink and sent its "
        f"content as the auth password; the daemon accepted -> transfer "
        f"succeeded. This is a content-disclosure primitive (the leaked "
        f"bytes are sent on the wire). Fix: refuse planted symlinks at "
        f"--password-file.")


# ---------------------------------------------------------------------------
# PARENT-component plant.
# ---------------------------------------------------------------------------
real_target = plants / 'parent_real_target'
real_target.mkdir()
(real_target / 'victim_in_target').write_text(CORRECT_PASSWORD + '\n')
os.chmod(real_target / 'victim_in_target', 0o600)

parent_plant = plants / 'parent_link'
os.symlink(real_target, parent_plant)
os.lchown(parent_plant, NOBODY_UID, NOBODY_UID)

# --password-file=$parent_plant/victim_in_target. Parent is the symlink; the
# kernel resolves the parent and reads victim_in_target inside. Plain O_NOFOLLOW
# does NOT defend (leaf-only); only the path walk with parent-symlink
# ownership checks defends here.
parent = push_with_pwfile(parent_plant / 'victim_in_target')
if parent.returncode == 0:
    test_fail(
        f"--password-file followed a planted PARENT-component symlink (owned "
        f"by uid {NOBODY_UID}): {parent_plant} -> {real_target} was traversed "
        f"and rsync read victim_in_target via the planted parent; the daemon "
        f"accepted the leaked password -> transfer succeeded. This is the "
        "/tmp/somedir/-class parent-flip case. Fix: per-component path walk "
        "refusing parent symlinks owned by untrusted uids.")

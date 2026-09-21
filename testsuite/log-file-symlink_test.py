#!/usr/bin/env python3
# --log-file=PATH must not follow planted symlinks at attacker-controlled
# path components.
#
# Threat: a privileged user (root or
# sysadmin) runs rsync with --log-file=PATH where PATH, or a parent component
# of PATH, lives in an attacker-influenceable directory (canonical case:
# /tmp/somedir/rsync.log). An unprivileged user plants a symlink there →
# rsync appends its log to an attacker-chosen file. The trivially exploitable
# landing is /root/.ssh/authorized_keys (append an SSH key → privesc).
#
# Both variants exercised:
#
#   LEAF plant:    --log-file=$plant where $plant is a symlink to a victim
#                  file. Refused by the leaf O_NOFOLLOW + the path walk's
#                  ownership check.
#
#   PARENT plant:  --log-file=$plant/rsync.log where $plant is a symlink to a
#                  victim directory. Refused by the path walk's parent-
#                  component ownership check (the /tmp/somedir/ case --
#                  plain O_NOFOLLOW alone is leaf-only and does NOT defend
#                  this; the path walk does).
#
# Defense model (the fix on pr-symlink-fix-privopens, per the audit):
# walk the operator-supplied path component-by-component using
# openat(O_PATH|O_NOFOLLOW), and for each component that IS a symlink,
# fstat() the symlink itself and allow only if st_uid is 0 or geteuid().
# Symlinks owned by an untrusted uid → ELOOP.
#
# Requires root (to lchown a symlink to a non-self uid -- the only way to
# simulate a planted attacker-owned symlink in-process). Skipped otherwise.

import os
import pwd
import subprocess

from rsyncfns import SCRATCHDIR, rmtree, rsync_argv, test_fail, test_skipped


if os.geteuid() != 0:
    test_skipped("requires root to plant a symlink owned by a non-root, "
                 "non-self uid (the attacker simulation)")

NOBODY_UID = None
for name in ('nobody', 'nfsnobody', 'daemon'):
    try:
        NOBODY_UID = pwd.getpwnam(name).pw_uid
        if NOBODY_UID != 0 and NOBODY_UID != os.geteuid():
            break
    except KeyError:
        continue
if NOBODY_UID is None or NOBODY_UID == 0 or NOBODY_UID == os.geteuid():
    test_skipped("no untrusted-uid user available for cross-uid plant "
                 "(tried nobody, nfsnobody, daemon)")


base = SCRATCHDIR / 'logfile'
src = base / 'src'
dest = base / 'dest'
plants = base / 'plants'

rmtree(base)
src.mkdir(parents=True)
(src / 'f').write_text("payload\n")
dest.mkdir(parents=True)
plants.mkdir(parents=True)


# ---------------------------------------------------------------------------
# LEAF plant
# ---------------------------------------------------------------------------
# A victim file the attacker wants rsync to append to. Owned by root (us),
# mode 0600, content we'll check is unchanged.
sentinel = plants / 'sentinel_leaf'
sentinel.write_text("LEAF_SENTINEL_INITIAL\n")
os.chmod(sentinel, 0o600)

# Plant: --log-file path is a symlink to the victim. The symlink itself is
# owned by NOBODY_UID (the attacker simulation).
leaf_path = plants / 'log_leaf'
os.symlink(sentinel, leaf_path)
os.lchown(leaf_path, NOBODY_UID, NOBODY_UID)

subprocess.run(
    rsync_argv('-a', '--log-file=' + str(leaf_path), f'{src}/', f'{dest}/'),
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

leaf_after = sentinel.read_text()
if leaf_after != "LEAF_SENTINEL_INITIAL\n":
    test_fail(
        f"--log-file followed a planted LEAF symlink (owned by uid "
        f"{NOBODY_UID}): {sentinel} changed from 'LEAF_SENTINEL_INITIAL' to "
        f"{leaf_after!r}. The privileged rsync appended its log into the "
        f"attacker-chosen file. Fix: refuse planted symlinks at --log-file.")


# ---------------------------------------------------------------------------
# PARENT-component plant (the /tmp/somedir/ case)
# ---------------------------------------------------------------------------
# A victim directory the attacker wants rsync to create files in. Owned by
# us, normally empty.
real_target = plants / 'parent_real_target'
real_target.mkdir()

# Plant: a directory-symlink owned by NOBODY_UID, pointing at the victim
# directory. --log-file=$plant/rsync.log resolves the parent (symlink) →
# kernel writes the log into the victim directory. The leaf "rsync.log"
# does not exist yet so plain O_NOFOLLOW on the leaf wouldn't reject anything;
# only the parent-component ownership check (the audit's owned-by-trusted-uid
# walk) defends here.
parent_plant = plants / 'parent_link'
os.symlink(real_target, parent_plant)
os.lchown(parent_plant, NOBODY_UID, NOBODY_UID)

log_via_parent = parent_plant / 'rsync.log'
subprocess.run(
    rsync_argv('-a', '--log-file=' + str(log_via_parent), f'{src}/', f'{dest}/'),
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

# Was a log file created in the victim directory via the planted symlink?
outside_log = real_target / 'rsync.log'
if outside_log.exists():
    test_fail(
        f"--log-file followed a planted PARENT-component symlink (owned by "
        f"uid {NOBODY_UID}): {parent_plant} -> {real_target} was traversed "
        f"and rsync's log was created at {outside_log}. This is the "
        "/tmp/somedir/-class parent-flip case that plain O_NOFOLLOW does NOT "
        "defend (O_NOFOLLOW is leaf-only). Fix: per-component path walk that "
        "refuses parent symlinks owned by an untrusted uid.")

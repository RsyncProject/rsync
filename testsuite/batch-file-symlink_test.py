#!/usr/bin/env python3
# --write-batch / --read-batch must not follow planted symlinks at
# attacker-controlled path components.
#
# Threat: batch.c:232,238,242 call
# do_open() (plain open() under the hood) without O_NOFOLLOW. Two attacks:
#
#   --write-batch: planted leaf symlink → root truncate+overwrite of an
#                  arbitrary file. Planted parent symlink → batch created
#                  inside an attacker-chosen dir.
#
#   --read-batch:  planted leaf symlink → rsync interprets attacker-chosen
#                  bytes as protocol data. Planted parent → same. Also: a
#                  planted FIFO/device at the read-batch path lets the
#                  attacker stream protocol bytes (the audit added a
#                  S_ISREG check on the opened fd as defense in depth).
#
# This test exercises both --write-batch variants (leaf and parent) where
# the leak signal is clear and direct: with the leak the victim file is
# truncated/overwritten or a new file appears under the planted parent;
# with the fix neither happens. --read-batch is covered by the same path-
# walk helper (the test for it would need a self-consistent batch payload
# to drive the receiver, which is excess machinery here).
#
# Requires root for the cross-uid plant; skips otherwise.

import os
import pwd
import subprocess

from rsyncfns import SCRATCHDIR, rmtree, rsync_argv, test_fail, test_skipped


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


base = SCRATCHDIR / 'batch'
src = base / 'src'
dest = base / 'dest'
plants = base / 'plants'

rmtree(base)
src.mkdir(parents=True)
(src / 'f').write_text("payload\n")
dest.mkdir(parents=True)
plants.mkdir(parents=True)


def run_write_batch(batch_plant_path):
    """Run rsync with --write-batch=batch_plant_path. The actual transfer
    succeeds; we only care whether the batch open follows the planted symlink."""
    # rsync writes both <name> and <name>.sh. Clean dest each run.
    rmtree(dest); dest.mkdir(parents=True)
    return subprocess.run(
        rsync_argv('-a', '--write-batch=' + str(batch_plant_path),
                   f'{src}/', f'{dest}/'),
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)


# ---------------------------------------------------------------------------
# LEAF plant — --write-batch path is a symlink to a victim file. Without the
# fix, the O_TRUNC|O_WRONLY|O_CREAT open truncates the victim and writes the
# batch data into it.
# ---------------------------------------------------------------------------
victim = plants / 'sentinel_leaf'
INITIAL_BYTES = b"VICTIM_INITIAL_CONTENTS\n"
victim.write_bytes(INITIAL_BYTES)
os.chmod(victim, 0o600)

leaf_plant = plants / 'write_batch_leaf'
os.symlink(victim, leaf_plant)
os.lchown(leaf_plant, NOBODY_UID, NOBODY_UID)

run_write_batch(leaf_plant)

# Read as bytes — rsync's batch data is binary protocol, not text.
leaf_after = victim.read_bytes()
if leaf_after != INITIAL_BYTES:
    test_fail(
        f"--write-batch followed a planted LEAF symlink (owned by uid "
        f"{NOBODY_UID}): victim {victim} was truncated/overwritten by the "
        f"O_TRUNC open. {len(INITIAL_BYTES)} initial bytes replaced by "
        f"{len(leaf_after)} bytes of attacker-influenceable batch data. "
        f"Fix: refuse planted symlinks at --write-batch.")


# ---------------------------------------------------------------------------
# PARENT-component plant — --write-batch=plant/batchname where plant is a
# symlink to a victim dir. The kernel resolves the parent (symlink), creates
# the batch INSIDE the victim dir. Plain O_NOFOLLOW on the leaf does NOT
# defend this; only the path-walk's parent-symlink ownership check does.
# ---------------------------------------------------------------------------
real_target = plants / 'parent_real_target'
real_target.mkdir()

parent_plant = plants / 'parent_link'
os.symlink(real_target, parent_plant)
os.lchown(parent_plant, NOBODY_UID, NOBODY_UID)

run_write_batch(parent_plant / 'batchfile')

# Did rsync create the batch (and/or its .sh wrapper) inside the planted dir?
created = sorted(p.name for p in real_target.iterdir())
if created:
    test_fail(
        f"--write-batch followed a planted PARENT-component symlink (owned "
        f"by uid {NOBODY_UID}): {parent_plant} -> {real_target} was "
        f"traversed and rsync wrote batch artefact(s) into the victim dir: "
        f"{created}. /tmp/somedir/-class parent-flip; plain O_NOFOLLOW does "
        "NOT defend. Fix: per-component path walk with parent-symlink "
        "ownership check.")


# ---------------------------------------------------------------------------
# --read-batch LEAF plant — the higher-power side of finding 1.6.  Read-batch
# content drives the receiver's protocol parser, so a planted symlink lets the
# attacker stream arbitrary protocol bytes into the receiver and have it
# create / modify files anywhere the receiver can write.  The leak signal in
# this test is direct: we first run a legitimate --write-batch to capture a
# real batch that creates a single canary file in the dest, then plant the
# read-batch path as a symlink to that legitimate batch.  Without the fix,
# the planted symlink is followed, rsync replays the legitimate batch from
# the planted target, and the canary appears in the destination.  With the
# fix, the planted symlink is refused (ELOOP via the owned-by-trusted-uid
# walk) and no canary appears.
# ---------------------------------------------------------------------------
legit_src = base / 'rb_src'
legit_src.mkdir()
(legit_src / 'rb_canary').write_bytes(b'AUDIT_READ_BATCH_LEAK_PAYLOAD')

# Capture a real batch by running write-batch normally (no symlinks in path).
legit_batch_dir = base / 'rb_legit_batch'
legit_batch_dir.mkdir()
legit_batch = legit_batch_dir / 'batch'
subprocess.run(
    rsync_argv('-a', f'--write-batch={legit_batch}',
               f'{legit_src}/', f'{dest}/rb_setup_unused/'),
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

# Now plant a symlink at the read-batch path, pointing at the legit batch.
rb_dest = base / 'rb_dest'
rmtree(rb_dest); rb_dest.mkdir()
rb_leaf_plant = plants / 'read_batch_leaf'
os.symlink(legit_batch, rb_leaf_plant)
os.lchown(rb_leaf_plant, NOBODY_UID, NOBODY_UID)

subprocess.run(
    rsync_argv('-a', f'--read-batch={rb_leaf_plant}', str(rb_dest) + '/'),
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

if (rb_dest / 'rb_canary').exists():
    test_fail(
        f"--read-batch followed a planted LEAF symlink (owned by uid "
        f"{NOBODY_UID}): rsync read the symlink's target as protocol data "
        f"and replayed the legitimate batch from it, creating "
        f"{rb_dest}/rb_canary in the destination.  The leaked batch could "
        "have written anywhere the receiver can write (higher-power "
        "primitive than --write-batch).  Fix: refuse planted symlinks at "
        "--read-batch.")


# ---------------------------------------------------------------------------
# --read-batch S_ISREG defense-in-depth — even when no symlink is involved,
# a planted FIFO / device / socket at the read-batch path lets an attacker
# stream protocol bytes into the receiver.  The audit's agreed defense is
# an fstat() S_ISREG check on the opened fd in addition to the path walk.
# We test it via a char-device node (a /dev/null clone) at the read-batch
# path: without the check rsync reads 0 bytes from the device and errors with
# a generic protocol message; with the check rsync errors with the specific
# "Batch file ... is not a regular file" message in stderr.
# ---------------------------------------------------------------------------
import stat as _stat

rb_dev = plants / 'read_batch_dev'
try:
    # Recreate /dev/null with its REAL device number so the node is openable
    # on every platform. A hardcoded makedev(1, 3) is only Linux's /dev/null;
    # on BSD/Solaris that major/minor is an unconfigured device, so rsync would
    # fail at open() (ENXIO) BEFORE reaching the S_ISREG check and the test
    # would mis-report a defended path as a failure.
    os.mknod(rb_dev, 0o600 | _stat.S_IFCHR, os.stat('/dev/null').st_rdev)
except OSError as e:
    test_skipped(
        f"cannot mknod a char device for S_ISREG sub-test ({e}); "
        "likely no CAP_MKNOD or the filesystem disallows devnodes")

rb_dev_dest = base / 'rb_dev_dest'
rmtree(rb_dev_dest); rb_dev_dest.mkdir()
proc = subprocess.run(
    rsync_argv('-a', f'--read-batch={rb_dev}', str(rb_dev_dest) + '/'),
    stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)

# The device must be refused.  The S_ISREG check yields the specific "is not a
# regular file" message; on some platforms (e.g. OpenBSD) opening the device
# node fails first with ENXIO, which is an equally valid refusal at the open.
# A clean (returncode 0) run would be the real failure -- rsync accepting the
# device as a batch file.
refused = ('is not a regular file' in proc.stderr
           or ('open error' in proc.stderr and proc.returncode != 0))
if not refused:
    test_fail(
        f"--read-batch did not refuse a non-regular batch path (char device at "
        f"{rb_dev}). rsync returncode {proc.returncode}, stderr: "
        f"{proc.stderr!r}. Fix: after the safe open, fstat() the fd and "
        "refuse !S_ISREG.")

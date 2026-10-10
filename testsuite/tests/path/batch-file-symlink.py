#!/usr/bin/env python3

import os
import pwd
import stat
import subprocess

from harness.rsync import SCRATCHDIR, rmtree, rsync_argv, test_fail, test_skipped

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
    rmtree(dest)
    dest.mkdir(parents=True)
    return subprocess.run(
        rsync_argv('-a', '--write-batch=' + str(batch_plant_path),
                   f'{src}/', f'{dest}/'),
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)

victim = plants / 'sentinel_leaf'
INITIAL_BYTES = b"VICTIM_INITIAL_CONTENTS\n"
victim.write_bytes(INITIAL_BYTES)
os.chmod(victim, 0o600)

leaf_plant = plants / 'write_batch_leaf'
os.symlink(victim, leaf_plant)
os.lchown(leaf_plant, NOBODY_UID, NOBODY_UID)

run_write_batch(leaf_plant)

leaf_after = victim.read_bytes()
if leaf_after != INITIAL_BYTES:
    test_fail(
        f'--write-batch replaced {victim} through untrusted uid {NOBODY_UID} '
        f'symlink: {len(INITIAL_BYTES)} bytes became {len(leaf_after)}')

real_target = plants / 'parent_real_target'
real_target.mkdir()

parent_plant = plants / 'parent_link'
os.symlink(real_target, parent_plant)
os.lchown(parent_plant, NOBODY_UID, NOBODY_UID)

run_write_batch(parent_plant / 'batchfile')

created = sorted(p.name for p in real_target.iterdir())
if created:
    test_fail(
        f'--write-batch traversed untrusted uid {NOBODY_UID} parent symlink '
        f'{parent_plant} -> {real_target} and created {created}')

legit_src = base / 'rb_src'
legit_src.mkdir()
(legit_src / 'rb_canary').write_bytes(b'AUDIT_READ_BATCH_LEAK_PAYLOAD')

legit_batch_dir = base / 'rb_legit_batch'
legit_batch_dir.mkdir()
legit_batch = legit_batch_dir / 'batch'
subprocess.run(
    rsync_argv('-a', f'--write-batch={legit_batch}',
               f'{legit_src}/', f'{dest}/rb_setup_unused/'),
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

rb_dest = base / 'rb_dest'
rmtree(rb_dest)
rb_dest.mkdir()
rb_leaf_plant = plants / 'read_batch_leaf'
os.symlink(legit_batch, rb_leaf_plant)
os.lchown(rb_leaf_plant, NOBODY_UID, NOBODY_UID)

subprocess.run(
    rsync_argv('-a', f'--read-batch={rb_leaf_plant}', str(rb_dest) + '/'),
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

if (rb_dest / 'rb_canary').exists():
    test_fail(
        f'--read-batch replayed data through untrusted uid {NOBODY_UID} '
        f'symlink and created {rb_dest}/rb_canary')

rb_dev = plants / 'read_batch_dev'
try:
    os.mknod(rb_dev, 0o600 | stat.S_IFCHR, os.stat('/dev/null').st_rdev)
except OSError as e:
    test_skipped(
        f"cannot mknod a char device for S_ISREG sub-test ({e}); "
        "likely no CAP_MKNOD or the filesystem disallows devnodes")

rb_dev_dest = base / 'rb_dev_dest'
rmtree(rb_dev_dest)
rb_dev_dest.mkdir()
proc = subprocess.run(
    rsync_argv('-a', f'--read-batch={rb_dev}', str(rb_dev_dest) + '/'),
    stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)

refused = ('is neither a regular file nor a FIFO' in proc.stderr
           or ('open error' in proc.stderr and proc.returncode != 0))
if not refused:
    test_fail(
        f"--read-batch did not refuse a non-regular batch path (char device at "
        f"{rb_dev}). rsync returncode {proc.returncode}, stderr: "
        f"{proc.stderr!r}. Fix: after the safe open, fstat() the fd and "
        "refuse !S_ISREG.")

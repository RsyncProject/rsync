#!/usr/bin/env python3

import os
import pwd
import subprocess

from harness.rsync import SCRATCHDIR, rmtree, rsync_argv, test_fail, test_skipped

if os.geteuid() != 0:
    test_skipped("requires root to plant a symlink owned by a non-self uid "
                 "(simulates 'root runs rsync' over an unprivileged user's dir)",
                 capability='cross_uid')

ATT_UID = None
for name in ('nobody', 'nfsnobody', 'daemon'):
    try:
        u = pwd.getpwnam(name).pw_uid
        if u != 0 and u != os.geteuid():
            ATT_UID = u
            break
    except KeyError:
        continue
if ATT_UID is None:
    test_skipped("no untrusted-uid user available for cross-uid plant", capability='cross_uid')

base = SCRATCHDIR / 'destchdir'
src = base / 'src'
dest = base / 'dest'
outside = base / 'outside'
rmtree(base)
(src / 'sub').mkdir(parents=True)
for i in range(4):
    (src / 'sub' / f'f{i}').write_text("payload\n")
outside.mkdir(parents=True)
dest.mkdir(parents=True)
os.symlink(outside, dest / 'sub')
os.lchown(dest / 'sub', ATT_UID, ATT_UID)

proc = subprocess.run(
    rsync_argv('-a', f'{src}/sub/', f'{dest}/sub/'),
    stdout=subprocess.DEVNULL,
    stderr=subprocess.PIPE,
    text=True,
)

if proc.returncode == 0:
    test_fail("attacker-owned destination symlink was not rejected")
if "refusing to follow a symlink owned by an untrusted user" not in proc.stderr:
    test_fail(
        "untrusted destination symlink failure omitted the actionable "
        f"diagnostic: {proc.stderr!r}"
    )

escaped = sorted(p.name for p in outside.iterdir())
if escaped:
    test_fail(
        "destination chdir followed an ATTACKER-owned symlink: the receiver "
        f"wrote files OUTSIDE the tree ({escaped}). change_dir() must refuse a "
        "dest symlink not owned by uid 0 or the euid.")

real = base / 'realsub'
real.mkdir()
os.unlink(dest / 'sub')
os.symlink(real, dest / 'sub')

subprocess.run(rsync_argv('-a', f'{src}/sub/', f'{dest}/sub/'),
               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

if not (real / 'f0').is_file():
    test_fail(
        "a root-owned symlinked destination (the /backup->/mnt/disk admin "
        "pattern) was NOT followed -- the cross-uid defense must still follow "
        "the operator's own symlinked dest.")

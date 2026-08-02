#!/usr/bin/env python3
# --copy-dest parent-component symlink-race: out-of-tree basis read-leak.
#
# With --copy-dest, rsync stats the alt-dest basis and, on an exact match, copies
# it into the destination instead of transferring.  basis_link_stat() refuses a
# foreign-owned basis symlink at stat time, but copy_file() opened an ABSOLUTE
# basis with bare do_open_nofollow (leaf-only -- parents followed).  A non-root
# attacker who flips a basis parent between a real dir and a foreign-owned symlink
# -> outside wins the window between the stat and the open: the copy then reads an
# out-of-tree file and writes its content into the destination (an attacker can
# read a root-readable file's content into a place they control).
#
# The fix re-resolves an absolute source's parents through the ownership walk in
# copy_file(), refusing the flipped foreign parent at open time.  RED on stock
# 3.4.x / --insecure-links, GREEN here.  Root+nobody gated.

import os
import subprocess
import time

from rsyncfns import (
    SCRATCHDIR, race_budget, find_attacker_uid,
    rmtree, rsync_argv, start_c_flipper, stop_flipper, test_fail, test_skipped,
)

if os.geteuid() != 0:
    test_skipped("requires root to plant a copy-dest symlink owned by a non-self uid")
ATT_UID = find_attacker_uid()
if ATT_UID is None:
    test_skipped("no untrusted-uid user available for cross-uid plant")

NFILES = 60
PINNED = 1000000000   # identical mtime so the basis is an exact match

base = SCRATCHDIR / 'copydest-readleak'
src = base / 'src'
dest = base / 'dest'
cdroot = base / 'cdroot'
cd = cdroot / 'cd'            # the basis dir the attacker flips
cdlink = cdroot / '.cdlink'   # symlink -> outside, swapped in for cd
outside = base / 'outside'

SECRET = 'OUT-OF-TREE-SECRET\n'


def build():
    rmtree(base)
    for d in (src, dest, cdroot, outside):
        d.mkdir(parents=True)
    cd.mkdir()
    for i in range(NFILES):
        (src / f'f{i}').write_text('SRC\n')
        (cd / f'f{i}').write_text('BAS\n')          # in-tree basis (exact match)
        (outside / f'f{i}').write_text(SECRET)        # out-of-tree content to leak
        for p in (src / f'f{i}', cd / f'f{i}', outside / f'f{i}'):
            os.utime(p, (PINNED, PINNED))
    os.symlink(str(outside), cdlink)
    os.lchown(cdlink, ATT_UID, ATT_UID)


def push():
    return subprocess.run(
        rsync_argv('-rt', f'--copy-dest={cd}', f'{src}/', f'{dest}/'),
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)


def leaked():
    for i in range(NFILES):
        try:
            if (dest / f'f{i}').read_text() == SECRET:
                return f'f{i}'
        except OSError:
            pass
    return None


# Positive control: a clean push with cd a real dir copies the in-tree basis.
build()
proc = push()
if proc.returncode != 0:
    test_fail("positive control: clean --copy-dest transfer failed "
              f"(rc={proc.returncode}):\n{proc.stdout or ''}")
for i in range(NFILES):
    target = dest / f'f{i}'
    if not target.is_file():
        test_fail(f"positive control: --copy-dest did not create {target}")
    got = target.read_text()
    if got != 'BAS\n':
        test_fail(
            "positive control: clean --copy-dest did not copy the in-tree "
            f"basis for {target} (got {got!r}); the test would not exercise "
            "copy_file()'s basis read path")

flip = start_c_flipper(cd, cdlink)
try:
    deadline = time.monotonic() + race_budget(10.0)
    while time.monotonic() < deadline:
        rmtree(dest)
        dest.mkdir()
        push()
        hit = leaked()
        if hit:
            test_fail(
                "--copy-dest parent symlink race: out-of-tree basis content was "
                f"read into the destination ({hit}) -- copy_file followed a "
                "flipped foreign-owned --copy-dest parent symlink (read-leak).")
finally:
    stop_flipper(flip)

# No read-leak within the race budget.

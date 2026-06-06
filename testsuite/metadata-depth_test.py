#!/usr/bin/env python3
"""Coverage of -p (perms), -t (times) and --chmod at depth.

Each attribute is set distinctively on a file AND a directory at every level of
a >=3-deep tree, then checked per entry after the transfer -- the metadata is
applied as a single-component operation on an entry whose parent chain the
resolver restructure rewrites, so it must be verified deep, not just at the
root.
"""

import os

from rsyncfns import (
    FROMDIR, TODIR,
    assert_mode, assert_mtime_close, make_tree, rmtree, run_rsync, test_fail,
    walk_dirs, walk_files,
)

src = FROMDIR
FILE_MODE = 0o640
DIR_MODE = 0o750
BASE_MTIME = 1_400_000_000          # a fixed, clearly-old timestamp


def seed():
    rmtree(src)
    rmtree(TODIR)
    make_tree(src, depth=3)
    for i, f in enumerate(walk_files(src)):
        os.chmod(f, FILE_MODE)
        os.utime(f, (BASE_MTIME + i * 100, BASE_MTIME + i * 100))
    for d in walk_dirs(src):
        os.chmod(d, DIR_MODE)


# --- -p preserves exact file and directory modes at every level -------------
seed()
run_rsync('-rlpt', f'{src}/', f'{TODIR}/')
for f in walk_files(src):
    assert_mode(TODIR / f.relative_to(src), FILE_MODE, label=f'-p file {f.name}')
for d in walk_dirs(src):
    assert_mode(TODIR / d.relative_to(src), DIR_MODE, label=f'-p dir {d.name}')

# --- -t preserves file mtimes at every level --------------------------------
for f in walk_files(src):
    rel = f.relative_to(src)
    assert_mtime_close(TODIR / rel, f.stat().st_mtime, label=f'-t {rel}')

# --- --chmod rewrites modes at every level ----------------------------------
seed()
run_rsync('-a', '--chmod=D710,F600', f'{src}/', f'{TODIR}/')
for f in walk_files(src):
    assert_mode(TODIR / f.relative_to(src), 0o600, label=f'--chmod file {f.name}')
for d in walk_dirs(src):
    assert_mode(TODIR / d.relative_to(src), 0o710, label=f'--chmod dir {d.name}')

print("metadata-depth: -p / -t / --chmod verified per entry at depth")

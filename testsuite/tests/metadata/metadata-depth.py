#!/usr/bin/env python3

import os

from harness.rsync import (
    FROMDIR,
    TODIR,
    assert_mode,
    assert_mtime_close,
    make_tree,
    rmtree,
    run_rsync,
    walk_dirs,
    walk_files,
)

src = FROMDIR
FILE_MODE = 0o640
DIR_MODE = 0o750
BASE_MTIME = 1_400_000_000

def seed():
    rmtree(src)
    rmtree(TODIR)
    make_tree(src, depth=3)
    for i, f in enumerate(walk_files(src)):
        os.chmod(f, FILE_MODE)
        os.utime(f, (BASE_MTIME + i * 100, BASE_MTIME + i * 100))
    for d in walk_dirs(src):
        os.chmod(d, DIR_MODE)

seed()
run_rsync('-rlpt', f'{src}/', f'{TODIR}/')
for f in walk_files(src):
    assert_mode(TODIR / f.relative_to(src), FILE_MODE, label=f'-p file {f.name}')
for d in walk_dirs(src):
    assert_mode(TODIR / d.relative_to(src), DIR_MODE, label=f'-p dir {d.name}')

for f in walk_files(src):
    rel = f.relative_to(src)
    assert_mtime_close(TODIR / rel, f.stat().st_mtime, label=f'-t {rel}')

seed()
run_rsync('-a', '--chmod=D710,F600', f'{src}/', f'{TODIR}/')
for f in walk_files(src):
    assert_mode(TODIR / f.relative_to(src), 0o600, label=f'--chmod file {f.name}')
for d in walk_dirs(src):
    assert_mode(TODIR / d.relative_to(src), 0o710, label=f'--chmod dir {d.name}')

print("metadata-depth: -p / -t / --chmod verified per entry at depth")

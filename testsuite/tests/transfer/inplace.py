#!/usr/bin/env python3

import os

from harness.rsync import (
    FROMDIR, TODIR,
    assert_same, make_tree, rmtree, run_rsync, test_fail,
)

src = FROMDIR
deep = os.path.join('d1', 'd2', 'd3', 'f3')

def seed():
    rmtree(src)
    rmtree(TODIR)
    make_tree(src, depth=3, data=True, data_size=200000)

def inode(path):
    return os.stat(path).st_ino

def modify_deep():
    p = src / deep
    data = bytearray(p.read_bytes())
    data[1000:1100] = bytes((b ^ 0xFF) for b in data[1000:1100])
    p.write_bytes(bytes(data))
    st = os.stat(p)
    os.utime(p, (st.st_atime, st.st_mtime + 100))

seed()
run_rsync('-a', f'{src}/', f'{TODIR}/')
ino_before = inode(TODIR / deep)

modify_deep()
run_rsync('-a', '--inplace', '--no-whole-file', f'{src}/', f'{TODIR}/')
assert_same(TODIR / deep, src / deep, label='inplace content')
if inode(TODIR / deep) != ino_before:
    test_fail("--inplace changed the destination inode at depth "
              f"({ino_before} -> {inode(TODIR / deep)})")

seed()
run_rsync('-a', f'{src}/', f'{TODIR}/')
ino_before = inode(TODIR / deep)

modify_deep()
run_rsync('-a', '--no-whole-file', f'{src}/', f'{TODIR}/')
assert_same(TODIR / deep, src / deep, label='default content')
if inode(TODIR / deep) == ino_before:
    test_fail("default (non-inplace) delta update unexpectedly kept the "
              "destination inode at depth -- temp+rename did not run")

print("inplace: same-inode update at depth verified; default replaces inode")

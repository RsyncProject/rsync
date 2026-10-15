#!/usr/bin/env python3
"""Coverage of --inplace at depth.

--inplace updates the destination file directly instead of writing a temp copy
and renaming it over the original, so across a delta update the destination
keeps the SAME inode. Without --inplace the receiver creates a fresh temp file
and renames it, giving the destination a NEW inode. Both behaviours hinge on
how the receiver resolves the destination directory and (for the default mode)
performs the temp->final rename, which the path restructure rewrites; verify
them on a file >=3 levels deep.
"""

import os

from rsyncfns import (
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
    # Rewrite a chunk in the middle so it is a genuine delta, not just a tail
    # append. Bump mtime by a clear margin (the whole test runs inside one
    # second, so a "now" touch would collide with the destination's mtime and
    # the size-unchanged file would be skipped by the quick check).
    p = src / deep
    data = bytearray(p.read_bytes())
    data[1000:1100] = bytes((b ^ 0xFF) for b in data[1000:1100])
    p.write_bytes(bytes(data))
    st = os.stat(p)
    os.utime(p, (st.st_atime, st.st_mtime + 100))


# --- --inplace keeps the destination inode across a delta update ------------
seed()
run_rsync('-a', f'{src}/', f'{TODIR}/')
ino_before = inode(TODIR / deep)

modify_deep()
run_rsync('-a', '--inplace', '--no-whole-file', f'{src}/', f'{TODIR}/')
assert_same(TODIR / deep, src / deep, label='inplace content')
if inode(TODIR / deep) != ino_before:
    test_fail("--inplace changed the destination inode at depth "
              f"({ino_before} -> {inode(TODIR / deep)})")

# --- control: the default (temp+rename) path replaces the inode -------------
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

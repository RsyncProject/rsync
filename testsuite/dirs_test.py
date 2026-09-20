#!/usr/bin/env python3
"""Coverage of -d / --dirs: transfer directories without recursing into them.

-d copies the entries directly named (or directly inside a trailing-slash
source): regular files at the top level are copied, and a top-level directory
is created as an empty directory -- its contents are NOT transferred. Verify
that on a tree that is several levels deep, only the top layer materialises.
"""

from rsyncfns import (
    FROMDIR, TODIR,
    assert_same, make_tree, rmtree, run_rsync, test_fail,
)

src = FROMDIR
rmtree(src)
rmtree(TODIR)
make_tree(src, depth=3)   # f0 at root; d1/{f1, d2/{f2, d3/f3}}

run_rsync('-d', f'{src}/', f'{TODIR}/')

# The top-level file is copied.
assert_same(TODIR / 'f0', src / 'f0', label='-d top-level file')
# The top-level directory is created...
if not (TODIR / 'd1').is_dir():
    test_fail("-d did not create the top-level directory")
# ...but NOT recursed into.
if (TODIR / 'd1' / 'f1').exists():
    test_fail("-d recursed into a directory (f1 should not exist)")
if list((TODIR / 'd1').iterdir()):
    test_fail("-d populated the directory; it should be empty")

print("dirs: -d copies the top layer without recursing")

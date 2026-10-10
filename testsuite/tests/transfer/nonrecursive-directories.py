#!/usr/bin/env python3

from harness.rsync import (
    FROMDIR, TODIR,
    assert_same, make_tree, rmtree, run_rsync, test_fail,
)

src = FROMDIR
rmtree(src)
rmtree(TODIR)
make_tree(src, depth=3)

run_rsync('-d', f'{src}/', f'{TODIR}/')

assert_same(TODIR / 'f0', src / 'f0', label='-d top-level file')
if not (TODIR / 'd1').is_dir():
    test_fail("-d did not create the top-level directory")
if (TODIR / 'd1' / 'f1').exists():
    test_fail("-d recursed into a directory (f1 should not exist)")
if list((TODIR / 'd1').iterdir()):
    test_fail("-d populated the directory; it should be empty")

print("--dirs copies the top layer without recursing")

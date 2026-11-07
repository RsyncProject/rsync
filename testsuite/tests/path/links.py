#!/usr/bin/env python3

import os

from harness.rsync import (
    FROMDIR, TODIR,
    assert_is_symlink, assert_same, make_tree, rmtree, run_rsync, test_fail,
)

src = FROMDIR
deepdir = os.path.join('d1', 'd2', 'd3')

def seed():
    rmtree(src)
    rmtree(TODIR)
    make_tree(src, depth=3)
    os.symlink('f3', src / deepdir / 'sl')
    (src / deepdir / 'realdir').mkdir()
    (src / deepdir / 'realdir' / 'inside').write_text("inside\n")
    os.symlink('realdir', src / deepdir / 'dirlink')

seed()
run_rsync('-rl', f'{src}/', f'{TODIR}/')
assert_is_symlink(TODIR / deepdir / 'sl', target='f3', label='-l file symlink')
assert_is_symlink(TODIR / deepdir / 'dirlink', target='realdir',
                  label='-l dir symlink')

seed()
run_rsync('-rL', f'{src}/', f'{TODIR}/')
if os.path.islink(TODIR / deepdir / 'sl'):
    test_fail("-L left a file symlink at depth instead of dereferencing it")
assert_same(TODIR / deepdir / 'sl', src / deepdir / 'f3', label='-L deref file')
if os.path.islink(TODIR / deepdir / 'dirlink'):
    test_fail("-L left a dir symlink at depth instead of dereferencing it")
assert_same(TODIR / deepdir / 'dirlink' / 'inside',
            src / deepdir / 'realdir' / 'inside', label='-L deref dir')

seed()
run_rsync('-rlk', f'{src}/', f'{TODIR}/')
if os.path.islink(TODIR / deepdir / 'dirlink'):
    test_fail("-k left the dir symlink as a symlink")
if not (TODIR / deepdir / 'dirlink').is_dir():
    test_fail("-k did not turn the dir symlink into a real directory")
assert_same(TODIR / deepdir / 'dirlink' / 'inside',
            src / deepdir / 'realdir' / 'inside', label='-k dir contents')
assert_is_symlink(TODIR / deepdir / 'sl', target='f3',
                  label='-k keeps the file symlink')

print("links: -l preserves, -L dereferences, -k follows dir-symlinks (at depth)")

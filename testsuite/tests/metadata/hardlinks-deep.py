#!/usr/bin/env python3

import os

from harness import metadata
from harness.rsync import (
    FROMDIR, TODIR,
    assert_hardlinked, assert_not_hardlinked, makepath, rmtree, run_rsync,
)

src = FROMDIR
metadata(features={'hardlinks'}, cost='expensive', mutates={'filesystem'}, tags={'metadata'})
a = os.path.join('a', 'aa', 'orig')
b = os.path.join('b', 'bb', 'hardlink')

rmtree(src)
rmtree(TODIR)
makepath(src / 'a' / 'aa', src / 'b' / 'bb')
(src / a).write_text("shared content across directories\n")
os.link(src / a, src / b)

run_rsync('-aH', f'{src}/', f'{TODIR}/')
assert_hardlinked(TODIR / a, TODIR / b, label='-H cross-dir hardlink')

rmtree(TODIR)
run_rsync('-a', f'{src}/', f'{TODIR}/')
assert_not_hardlinked(TODIR / a, TODIR / b, label='no -H => separate inodes')

print("hardlinks-deep: -H preserves a cross-directory hard link at depth")

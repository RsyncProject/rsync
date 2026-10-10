#!/usr/bin/env python3
"""Coverage of -H across directory boundaries.

hardlinks_test.py exercises -H on sibling files at the tree root; this
companion checks that -H preserves a hard link whose two names live in
DIFFERENT directories several levels deep (the cross-directory case the
resolver restructure touches), and that without -H the names become
independent inodes.
"""

from rsyncfns import (
    FROMDIR, TODIR,
    assert_hardlinked, assert_not_hardlinked, makepath, rmtree, run_rsync,
)
import os

src = FROMDIR
a = os.path.join('a', 'aa', 'orig')
b = os.path.join('b', 'bb', 'hardlink')

rmtree(src)
rmtree(TODIR)
makepath(src / 'a' / 'aa', src / 'b' / 'bb')
(src / a).write_text("shared content across directories\n")
os.link(src / a, src / b)            # one inode, two names in different dirs

# -H preserves the cross-directory hard link.
run_rsync('-aH', f'{src}/', f'{TODIR}/')
assert_hardlinked(TODIR / a, TODIR / b, label='-H cross-dir hardlink')

# Without -H the two names are copied as independent files.
rmtree(TODIR)
run_rsync('-a', f'{src}/', f'{TODIR}/')
assert_not_hardlinked(TODIR / a, TODIR / b, label='no -H => separate inodes')

print("hardlinks-deep: -H preserves a cross-directory hard link at depth")

#!/usr/bin/env python3
# rsync -aR of a source file whose path goes through an IN-TREE symlinked parent
# directory must transfer the file -- the symlink is part of the operator's own
# source tree, which 3.4.x follows.
#
# A/B-discovered 3.5 regression (abdiff: 3.5 vs 3.4.4): an early per-component
# O_NOFOLLOW resolver refused to traverse the in-tree symlinked parent that -R
# keeps in the path, so the content open failed (exit 23, the file lost; a
# near-silent partial copy).  The secure resolver now follows in-tree directory
# symlinks on every platform -- it readlinks the symlink and walks its target off
# held dirfds -- so this transfers uniformly.  3.4.x is unaffected everywhere.
#
# Related to the fixed `rsync -aR /abs` bug (sender content-open confinement),
# but a distinct trigger: an in-tree symlinked *parent component* under -R.
# Plain transfer, no root.

import os

from rsyncfns import SCRATCHDIR, assert_same, makepath, rmtree, run_rsync, test_fail

base = SCRATCHDIR / 'rsymparent'
rmtree(base)
makepath(base / 'src' / 'real' / 'sub', base / 'dest')
(base / 'src' / 'real' / 'sub' / 'file').write_text('payload data\n')
os.symlink('real', base / 'src' / 'link')          # in-tree dir-symlink: link -> real

os.chdir(base)                                       # -R mirrors the path as given
run_rsync('-aR', 'src/link/sub/file', 'dest/')      # check=True: resolver follows the in-tree symlink

landed = base / 'dest' / 'src' / 'link' / 'sub' / 'file'
if not landed.is_file():
    test_fail(f"-aR through an in-tree symlinked parent dropped the file: {landed} missing")
assert_same(base / 'src' / 'real' / 'sub' / 'file', landed, label='content')

print("relative-symlinked-parent: -aR transfers a file under an in-tree symlinked parent dir")

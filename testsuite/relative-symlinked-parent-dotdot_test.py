#!/usr/bin/env python3
# rsync -aR through an in-tree symlinked parent whose target climbs with ".."
# but stays BENEATH the transfer anchor must transfer the file.  This is the
# RESOLVE_BENEATH rule: an in-tree ".." that does not rise above the root is
# allowed (kernel paths follow it, and 3.4.x followed it).
#
# Companion to relative-symlinked-parent_test.py (whose symlink target has no
# ".."): the portable per-component fallback (the BSDs / Solaris / Cygwin, or a
# Linux build configured --disable-openat2) originally refused EVERY symlink,
# then -- after the #715 fix -- still refused any symlink target containing
# "..", even a safe internal climb.  Both are now followed via a dirfd stack
# that pops ".." back to the pinned parent (never above the anchor), so this
# passes everywhere while genuine escapes (../ above the anchor, absolute
# targets) stay refused -- see secure-relpath-validation / the *-symlink-race
# tests.  Plain transfer, no root.

import os

from rsyncfns import SCRATCHDIR, assert_same, makepath, rmtree, run_rsync, test_fail

base = SCRATCHDIR / 'rsymparentdd'
rmtree(base)
# src/real/sub/file is the payload; src/deep/alias is an in-tree symlink whose
# target "../real" climbs out of src/deep back to src/ (still beneath src/).
makepath(base / 'src' / 'real' / 'sub', base / 'src' / 'deep', base / 'dest')
(base / 'src' / 'real' / 'sub' / 'file').write_text('payload data\n')
os.symlink('../real', base / 'src' / 'deep' / 'alias')   # in-tree ".." dir-symlink

os.chdir(base)                                            # -R mirrors the path as given
run_rsync('-aR', 'src/deep/alias/sub/file', 'dest/')     # check=True: fallback exits 23 if unfixed

landed = base / 'dest' / 'src' / 'deep' / 'alias' / 'sub' / 'file'
if not landed.is_file():
    test_fail(f"-aR through an in-tree '..' symlinked parent dropped the file: {landed} missing")
assert_same(base / 'src' / 'real' / 'sub' / 'file', landed, label='content')

print("relative-symlinked-parent-dotdot: -aR follows an in-tree '..' symlinked parent beneath the anchor")

#!/usr/bin/env python3
# --keep-dirlinks (-K) into a symlinked destination directory must follow the
# symlink and write into the real dir -- that is the entire purpose of -K
# (treat a symlinked dir on the receiver as a dir; the #715 family).
#
# A/B-discovered 3.5 regression (abdiff: 3.5 vs 3.4.4): an early per-component
# O_NOFOLLOW resolver refused the in-tree dir-symlink that -K is meant to follow,
# so the transfer failed (Not a directory -> exit 23, the directory's files lost;
# near-silent).  The secure resolver now follows in-tree directory symlinks on
# every platform, so -K writes through the symlinked dest dir uniformly; 3.4.x is
# unaffected.  Plain transfer, no root.

import os

from rsyncfns import SCRATCHDIR, assert_same, makepath, rmtree, run_rsync, test_fail

base = SCRATCHDIR / 'kdl'
src = base / 'src'
dest = base / 'dest'
rmtree(base)
makepath(src / 'dir', dest / 'realdir')
(src / 'dir' / 'f1').write_text('one\n')
(src / 'dir' / 'f2').write_text('two\n')
(src / 'top.txt').write_text('top\n')
# The receiver already has the destination subdir as an in-tree symlink to a
# real directory -- exactly what --keep-dirlinks is for.
os.symlink('realdir', dest / 'dir')

run_rsync('-aK', f'{src}/', f'{dest}/')   # check=True: resolver follows the in-tree dest symlink

real = dest / 'realdir'
for n in ('f1', 'f2'):
    if not (real / n).is_file():
        test_fail(f"-K did not follow the symlinked dest dir: {real / n} missing")
    assert_same(src / 'dir' / n, real / n, label=f'-K content {n}')

print("keep-dirlinks-symlinked-dest: -K follows a symlinked destination directory")

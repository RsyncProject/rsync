#!/usr/bin/env python3

import os

from harness.rsync import SCRATCHDIR, assert_same, makepath, rmtree, run_rsync, test_fail

base = SCRATCHDIR / 'kdl'
src = base / 'src'
dest = base / 'dest'
rmtree(base)
makepath(src / 'dir', dest / 'realdir')
(src / 'dir' / 'f1').write_text('one\n')
(src / 'dir' / 'f2').write_text('two\n')
(src / 'top.txt').write_text('top\n')
os.symlink('realdir', dest / 'dir')

run_rsync('-aK', f'{src}/', f'{dest}/')

real = dest / 'realdir'
for n in ('f1', 'f2'):
    if not (real / n).is_file():
        test_fail(f"-K did not follow the symlinked dest dir: {real / n} missing")
    assert_same(src / 'dir' / n, real / n, label=f'-K content {n}')

print("keep-dirlinks-symlinked-dest: -K follows a symlinked destination directory")

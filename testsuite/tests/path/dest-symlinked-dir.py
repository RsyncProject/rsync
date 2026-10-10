#!/usr/bin/env python3

import os

from harness.rsync import SCRATCHDIR, assert_same, rmtree, run_rsync, test_fail

base = SCRATCHDIR / 'dest-symlinked'
src = base / 'src'
real = base / 'realdest'
link = base / 'backup'
rmtree(base)
src.mkdir(parents=True)
real.mkdir(parents=True)
(src / 'f.txt').write_text('content\n')
(src / 'sub').mkdir()
(src / 'sub' / 'g.txt').write_text('deep\n')
os.symlink(str(real), str(link))

run_rsync('-a', f'{src}/', f'{link}/')

for rel in ('f.txt', 'sub/g.txt'):
    got = real / rel
    if not got.is_file():
        test_fail("transfer through an absolute symlinked dest did not land in "
                  f"the real directory: {got} missing")
    assert_same(src / rel, got, label=rel)

print("dest-symlinked-dir: a non-daemon receiver follows an absolute symlinked dest")

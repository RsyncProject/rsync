#!/usr/bin/env python3

import os

from harness.rsync import (
    FROMDIR, TODIR,
    allocated_size, assert_same, makepath, rmtree, run_rsync, test_fail, test_skipped,
)

src = FROMDIR
deep = os.path.join('d1', 'd2', 'd3', 'holey')
SIZE = 4 * 1024 * 1024

def make_sparse(path):
    with open(path, 'wb') as f:
        f.write(b'head')
        f.seek(SIZE - 4)
        f.write(b'tail')

rmtree(src)
rmtree(TODIR)
makepath(src / 'd1' / 'd2' / 'd3')
make_sparse(src / deep)

if allocated_size(src / deep) >= SIZE:
    test_skipped("source filesystem did not create a sparse file", capability='sparse')

run_rsync('-a', '-S', f'{src}/', f'{TODIR}/')
assert_same(TODIR / deep, src / deep, label='-S content')
if allocated_size(TODIR / deep) >= SIZE:
    test_fail(f"-S did not preserve the hole at depth "
              f"(allocated {allocated_size(TODIR / deep)} for a {SIZE}-byte file)")

rmtree(TODIR)
run_rsync('-a', '--no-sparse', f'{src}/', f'{TODIR}/')
assert_same(TODIR / deep, src / deep, label='no-sparse content')

print("sparse: -S preserves a deep hole; content correct with and without it")

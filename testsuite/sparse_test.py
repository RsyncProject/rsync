#!/usr/bin/env python3
"""Coverage of -S (--sparse) at depth.

A file with a large hole, several directory levels deep, should arrive sparse
(its allocated blocks much smaller than its apparent size) and byte-identical
when copied with -S.

We do NOT assert the converse (that a plain copy fills the hole): whether a
zero-run is stored as a hole when -S is absent is the filesystem's choice, not
rsync's -- ZFS/APFS transparently sparsify zero blocks, so a --no-sparse copy
can legitimately stay sparse there.
"""

import os

from rsyncfns import (
    FROMDIR, TODIR,
    assert_same, makepath, rmtree, run_rsync, test_fail, test_skipped,
)

src = FROMDIR
deep = os.path.join('d1', 'd2', 'd3', 'holey')
SIZE = 4 * 1024 * 1024


def make_sparse(path):
    with open(path, 'wb') as f:
        f.write(b'head')
        f.seek(SIZE - 4)
        f.write(b'tail')


def allocated(path):
    return os.stat(path).st_blocks * 512


rmtree(src)
rmtree(TODIR)
makepath(src / 'd1' / 'd2' / 'd3')
make_sparse(src / deep)

# Confirm the source filesystem actually made a sparse file; otherwise the
# whole premise (and any dest comparison) is meaningless here.
if allocated(src / deep) >= SIZE:
    test_skipped("source filesystem did not create a sparse file")

# --- with -S the hole is preserved at the destination -----------------------
run_rsync('-a', '-S', f'{src}/', f'{TODIR}/')
assert_same(TODIR / deep, src / deep, label='-S content')
if allocated(TODIR / deep) >= SIZE:
    test_fail(f"-S did not preserve the hole at depth "
              f"(allocated {allocated(TODIR / deep)} for a {SIZE}-byte file)")

# --- a plain copy reproduces the content too (allocation is FS-defined) ------
rmtree(TODIR)
run_rsync('-a', '--no-sparse', f'{src}/', f'{TODIR}/')
assert_same(TODIR / deep, src / deep, label='no-sparse content')

print("sparse: -S preserves a deep hole; content correct with and without it")

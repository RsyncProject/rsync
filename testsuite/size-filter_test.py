#!/usr/bin/env python3
"""Coverage of --max-size / --min-size at depth.

A small and a large file at every level; --max-size must transfer only the
small ones and --min-size only the large ones, the selection holding all the
way down the tree.
"""

import os

from rsyncfns import (
    FROMDIR, TODIR,
    assert_not_exists, assert_same, make_data_file, rmtree, run_rsync,
)

src = FROMDIR
SMALL = 500
LARGE = 5000


def seed():
    rmtree(src)
    rmtree(TODIR)
    cur = src
    for lvl in range(4):
        cur.mkdir(parents=True, exist_ok=True)
        make_data_file(cur / f'small{lvl}', SMALL)
        make_data_file(cur / f'large{lvl}', LARGE)
        cur = cur / f'd{lvl + 1}'


# --- --max-size keeps only the small files at every level -------------------
# Compare content (not just existence) so a kept file is proven to be the
# transferred source, not an empty/stale placeholder.
seed()
run_rsync('-a', '--max-size=1000', f'{src}/', f'{TODIR}/')
dcur, scur = TODIR, src
for lvl in range(4):
    assert_same(dcur / f'small{lvl}', scur / f'small{lvl}',
                label=f'--max-size kept small L{lvl}')
    assert_not_exists(dcur / f'large{lvl}', label=f'--max-size dropped large L{lvl}')
    dcur, scur = dcur / f'd{lvl + 1}', scur / f'd{lvl + 1}'

# --- --min-size keeps only the large files at every level -------------------
seed()
run_rsync('-a', '--min-size=1000', f'{src}/', f'{TODIR}/')
dcur, scur = TODIR, src
for lvl in range(4):
    assert_same(dcur / f'large{lvl}', scur / f'large{lvl}',
                label=f'--min-size kept large L{lvl}')
    assert_not_exists(dcur / f'small{lvl}', label=f'--min-size dropped small L{lvl}')
    dcur, scur = dcur / f'd{lvl + 1}', scur / f'd{lvl + 1}'

print("size-filter: --max-size / --min-size select correctly at depth")

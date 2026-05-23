#!/usr/bin/env python3
"""Coverage of --max-size / --min-size at depth.

A small and a large file at every level; --max-size must transfer only the
small ones and --min-size only the large ones, the selection holding all the
way down the tree.
"""

import os

from rsyncfns import (
    FROMDIR, TODIR,
    assert_exists, assert_not_exists, make_data_file, rmtree, run_rsync,
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
seed()
run_rsync('-a', '--max-size=1000', f'{src}/', f'{TODIR}/')
cur = TODIR
for lvl in range(4):
    assert_exists(cur / f'small{lvl}', label=f'--max-size kept small L{lvl}')
    assert_not_exists(cur / f'large{lvl}', label=f'--max-size dropped large L{lvl}')
    cur = cur / f'd{lvl + 1}'

# --- --min-size keeps only the large files at every level -------------------
seed()
run_rsync('-a', '--min-size=1000', f'{src}/', f'{TODIR}/')
cur = TODIR
for lvl in range(4):
    assert_exists(cur / f'large{lvl}', label=f'--min-size kept large L{lvl}')
    assert_not_exists(cur / f'small{lvl}', label=f'--min-size dropped small L{lvl}')
    cur = cur / f'd{lvl + 1}'

print("size-filter: --max-size / --min-size select correctly at depth")

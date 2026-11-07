#!/usr/bin/env python3

from harness.rsync import (
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

seed()
run_rsync('-a', '--max-size=1000', f'{src}/', f'{TODIR}/')
dcur, scur = TODIR, src
for lvl in range(4):
    assert_same(dcur / f'small{lvl}', scur / f'small{lvl}',
                label=f'--max-size kept small L{lvl}')
    assert_not_exists(dcur / f'large{lvl}', label=f'--max-size dropped large L{lvl}')
    dcur, scur = dcur / f'd{lvl + 1}', scur / f'd{lvl + 1}'

seed()
run_rsync('-a', '--min-size=1000', f'{src}/', f'{TODIR}/')
dcur, scur = TODIR, src
for lvl in range(4):
    assert_same(dcur / f'large{lvl}', scur / f'large{lvl}',
                label=f'--min-size kept large L{lvl}')
    assert_not_exists(dcur / f'small{lvl}', label=f'--min-size dropped small L{lvl}')
    dcur, scur = dcur / f'd{lvl + 1}', scur / f'd{lvl + 1}'

print("size-filter: --max-size / --min-size select correctly at depth")

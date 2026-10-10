#!/usr/bin/env python3

from harness.rsync import FROMDIR, TODIR, assert_exists, assert_not_exists, makepath, rmtree, run_rsync

src = FROMDIR

def seed_ext():
    rmtree(src)
    rmtree(TODIR)
    cur = src
    for lvl in range(4):
        cur.mkdir(parents=True, exist_ok=True)
        (cur / f'keep{lvl}.txt').write_text(f'txt {lvl}\n')
        (cur / f'drop{lvl}.log').write_text(f'log {lvl}\n')
        cur = cur / f'd{lvl + 1}'

seed_ext()
run_rsync('-a', '--exclude=*.log', f'{src}/', f'{TODIR}/')
cur = TODIR
for lvl in range(4):
    assert_exists(cur / f'keep{lvl}.txt', label=f'--exclude kept txt L{lvl}')
    assert_not_exists(cur / f'drop{lvl}.log', label=f'--exclude dropped log L{lvl}')
    cur = cur / f'd{lvl + 1}'

seed_ext()
run_rsync('-a', '--include=*/', '--include=*.txt', '--exclude=*',
          f'{src}/', f'{TODIR}/')
cur = TODIR
for lvl in range(4):
    assert_exists(cur / f'keep{lvl}.txt', label=f'--include txt L{lvl}')
    assert_not_exists(cur / f'drop{lvl}.log', label=f'--include excluded log L{lvl}')
    cur = cur / f'd{lvl + 1}'

rmtree(src)
rmtree(TODIR)
makepath(src / 'd1' / 'd2' / 'd3')
for rel in ('secret.top', 'd1/secret.mid', 'd1/d2/secret.deep',
            'd1/d2/d3/secret.deeper'):
    (src / rel).write_text('x\n')
(src / 'd1' / 'd2' / '.rsync-filter').write_text('- secret*\n')

run_rsync('-aF', f'{src}/', f'{TODIR}/')
assert_exists(TODIR / 'secret.top', label='-F above merge dir')
assert_exists(TODIR / 'd1' / 'secret.mid', label='-F above merge dir')
assert_not_exists(TODIR / 'd1' / 'd2' / 'secret.deep', label='-F at merge dir')
assert_not_exists(TODIR / 'd1' / 'd2' / 'd3' / 'secret.deeper',
                  label='-F below merge dir')

print("filter-depth: --exclude/--include precedence and -F per-dir merge at depth")

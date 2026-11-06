#!/usr/bin/env python3
"""Coverage of --exclude / --include / --filter / -F at depth.

The interesting case for the resolver restructure is a per-directory merge file
(-F reads .rsync-filter from each directory as it descends): the rule set is
loaded from a file several levels deep and must apply to that directory and
below, but not above. Also check plain --exclude / --include precedence on
files spread through the tree.
"""

import os

from rsyncfns import (
    FROMDIR, TODIR,
    assert_exists, assert_not_exists, assert_same, makepath, rmtree, run_rsync,
)

src = FROMDIR


def seed_ext():
    """A tree with a .log and a .txt at every level."""
    rmtree(src)
    rmtree(TODIR)
    cur = src
    for lvl in range(4):
        cur.mkdir(parents=True, exist_ok=True)
        (cur / f'keep{lvl}.txt').write_text(f'txt {lvl}\n')
        (cur / f'drop{lvl}.log').write_text(f'log {lvl}\n')
        cur = cur / f'd{lvl + 1}'


# --- --exclude drops matching files at every level --------------------------
seed_ext()
run_rsync('-a', '--exclude=*.log', f'{src}/', f'{TODIR}/')
cur = TODIR
for lvl in range(4):
    assert_exists(cur / f'keep{lvl}.txt', label=f'--exclude kept txt L{lvl}')
    assert_not_exists(cur / f'drop{lvl}.log', label=f'--exclude dropped log L{lvl}')
    cur = cur / f'd{lvl + 1}'

# --- --include before --exclude='*' keeps only .txt at every level ----------
seed_ext()
run_rsync('-a', '--include=*/', '--include=*.txt', '--exclude=*',
          f'{src}/', f'{TODIR}/')
cur = TODIR
for lvl in range(4):
    assert_exists(cur / f'keep{lvl}.txt', label=f'--include txt L{lvl}')
    assert_not_exists(cur / f'drop{lvl}.log', label=f'--include excluded log L{lvl}')
    cur = cur / f'd{lvl + 1}'

# --- -F per-directory merge file loaded from a deep directory ---------------
# .rsync-filter at d1/d2 excludes "secret*" for d1/d2 and below only.
rmtree(src)
rmtree(TODIR)
makepath(src / 'd1' / 'd2' / 'd3')
for rel in ('secret.top', 'd1/secret.mid', 'd1/d2/secret.deep',
            'd1/d2/d3/secret.deeper'):
    (src / rel).write_text('x\n')
(src / 'd1' / 'd2' / '.rsync-filter').write_text('- secret*\n')

run_rsync('-aF', f'{src}/', f'{TODIR}/')
# Above the merge file: not affected.
assert_exists(TODIR / 'secret.top', label='-F above merge dir')
assert_exists(TODIR / 'd1' / 'secret.mid', label='-F above merge dir')
# At and below the merge file: excluded.
assert_not_exists(TODIR / 'd1' / 'd2' / 'secret.deep', label='-F at merge dir')
assert_not_exists(TODIR / 'd1' / 'd2' / 'd3' / 'secret.deeper',
                  label='-F below merge dir')

print("filter-depth: --exclude/--include precedence and -F per-dir merge at depth")

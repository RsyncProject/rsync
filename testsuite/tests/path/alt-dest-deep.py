#!/usr/bin/env python3

import os

from harness.rsync import (
    FROMDIR, SCRATCHDIR, TODIR, assert_exists, assert_hardlinked, assert_not_exists, assert_not_hardlinked, assert_same, make_tree, rmtree, run_rsync, walk_files, test_fail,
)

src = FROMDIR
ref = SCRATCHDIR / 'altref'

rmtree(src)
rmtree(ref)
rmtree(TODIR)

make_tree(src, depth=3, data=True)

run_rsync('-a', f'{src}/', f'{ref}/')

changed = os.path.join('d1', 'd2', 'd3', 'f3')
with open(src / changed, 'ab') as f:
    f.write(b'a changed deep tail\n')

rels = [p.relative_to(src) for p in walk_files(src)]
if os.path.join('d1', 'd2', 'd3', 'f3') not in [str(r) for r in rels]:
    test_fail('deep alternate-destination fixture is incomplete')

def run_to(opt):
    rmtree(TODIR)
    run_rsync('-a', f'--{opt}={ref}', f'{src}/', f'{TODIR}/')

run_to('link-dest')
for rel in rels:
    d, r = TODIR / rel, ref / rel
    if str(rel) == changed:
        assert_not_hardlinked(d, r, label=f'link-dest changed {rel}')
        assert_same(d, src / rel, label=f'link-dest changed {rel}')
    else:
        assert_hardlinked(d, r, label=f'link-dest unchanged {rel}')

run_to('copy-dest')
for rel in rels:
    d, r = TODIR / rel, ref / rel
    assert_exists(d, label=f'copy-dest {rel}')
    assert_same(d, src / rel, label=f'copy-dest {rel}')
    assert_not_hardlinked(d, r, label=f'copy-dest {rel}')

run_to('compare-dest')
for rel in rels:
    d = TODIR / rel
    if str(rel) == changed:
        assert_exists(d, label=f'compare-dest changed {rel}')
        assert_same(d, src / rel, label=f'compare-dest changed {rel}')
    else:
        assert_not_exists(d, label=f'compare-dest unchanged {rel}')

print("alt-dest-deep: link-dest/copy-dest/compare-dest verified at depth")

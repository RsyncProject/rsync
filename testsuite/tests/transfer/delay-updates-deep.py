#!/usr/bin/env python3

import os

from harness.rsync import (
    FROMDIR, TODIR,
    assert_same, make_tree, rmtree, run_rsync, test_fail, walk_dirs,
    walk_files,
)

src = FROMDIR
deepdir = os.path.join('d1', 'd2', 'd3')

def no_staging_left():
    leftover = [p for p in walk_dirs(TODIR) if p.name == '.~tmp~']
    if leftover:
        test_fail(f"--delay-updates left staging dirs behind: {leftover}")

rmtree(src)
rmtree(TODIR)
make_tree(src, depth=3, data=True, data_size=4096)
rels = [p.relative_to(src) for p in walk_files(src)]

run_rsync('-a', '--delay-updates', f'{src}/', f'{TODIR}/')
for rel in rels:
    assert_same(TODIR / rel, src / rel, label=f'delay-updates initial {rel}')
no_staging_left()

for rel in rels:
    with open(src / rel, 'ab') as f:
        f.write(b'\nupdated content\n')

stage = TODIR / deepdir / '.~tmp~'
stage.mkdir(parents=True, exist_ok=True)
(stage / 'f3').write_bytes(b'stale staged junk\n')

run_rsync('-a', '--delay-updates', f'{src}/', f'{TODIR}/')
for rel in rels:
    assert_same(TODIR / rel, src / rel, label=f'delay-updates update {rel}')
no_staging_left()

print("delay-updates-deep: staging + clean overwrite verified at depth")

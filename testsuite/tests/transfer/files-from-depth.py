#!/usr/bin/env python3

from harness.rsync import (
    FROMDIR, SCRATCHDIR, TODIR,
    assert_exists, assert_not_exists, assert_same, make_tree, rmtree,
    run_rsync,
)

src = FROMDIR
listed = ['d1/f1', 'd1/d2/d3/f3']
unlisted = ['f0', 'd1/d2/f2']

def seed():
    rmtree(src)
    rmtree(TODIR)
    make_tree(src, depth=3)

seed()
lf = SCRATCHDIR / 'files.lst'
lf.write_text('\n'.join(listed) + '\n')
run_rsync('-a', f'--files-from={lf}', f'{src}/', f'{TODIR}/')
for rel in listed:
    assert_same(TODIR / rel, src / rel, label=f'--files-from {rel}')
for rel in unlisted:
    assert_not_exists(TODIR / rel, label=f'--files-from excluded {rel}')

rmtree(TODIR)
lf0 = SCRATCHDIR / 'files0.lst'
lf0.write_bytes(b'\0'.join(p.encode() for p in listed) + b'\0')
run_rsync('-a', '--from0', f'--files-from={lf0}', f'{src}/', f'{TODIR}/')
for rel in listed:
    assert_same(TODIR / rel, src / rel, label=f'--from0 {rel}')
for rel in unlisted:
    assert_not_exists(TODIR / rel, label=f'--from0 excluded {rel}')

rmtree(TODIR)
(src / '#ignored').write_text('hash ignored\n')
(src / ';ignored').write_text('semi ignored\n')
commented = SCRATCHDIR / 'files-commented.lst'
commented.write_text('\n'.join(['', ';ignored', '#ignored', *listed]) + '\n')
run_rsync('-a', f'--files-from={commented}', f'{src}/', f'{TODIR}/')
for rel in listed:
    assert_same(TODIR / rel, src / rel, label=f'--files-from comment list {rel}')
for rel in unlisted:
    assert_not_exists(TODIR / rel, label=f'--files-from comment list excluded {rel}')
for rel in ['#ignored', ';ignored']:
    assert_not_exists(TODIR / rel, label=f'--files-from comment list skipped {rel}')

rmtree(TODIR)
comments0 = SCRATCHDIR / 'files-comments0.lst'
comments0.write_bytes(
    b'\0;ignored\0#ignored\0' + b'\0'.join(p.encode() for p in listed) + b'\0')
run_rsync('-a', '--from0', f'--files-from={comments0}', f'{src}/', f'{TODIR}/')
for rel in listed:
    assert_same(TODIR / rel, src / rel, label=f'--from0 comment list {rel}')
for rel in unlisted:
    assert_not_exists(TODIR / rel, label=f'--from0 comment list excluded {rel}')
for rel in ['#ignored', ';ignored']:
    assert_not_exists(TODIR / rel, label=f'--from0 comment list skipped {rel}')

seed()
(src / 'a.skip').write_text('s\n')
(src / 'd1' / 'd2' / 'a.skip').write_text('s\n')
ef = SCRATCHDIR / 'excl.lst'
ef.write_text('*.skip\n')
run_rsync('-a', f'--exclude-from={ef}', f'{src}/', f'{TODIR}/')
assert_not_exists(TODIR / 'a.skip', label='--exclude-from top')
assert_not_exists(TODIR / 'd1' / 'd2' / 'a.skip', label='--exclude-from deep')
assert_same(TODIR / 'd1' / 'd2' / 'f2', src / 'd1' / 'd2' / 'f2',
            label='--exclude-from kept others')

seed()
(src / 'd1' / 'd2' / 'k.keepme').write_text('k\n')
inc = SCRATCHDIR / 'inc.lst'
inc.write_text('*/\n*.keepme\n')
run_rsync('-a', f'--include-from={inc}', '--exclude=*', f'{src}/', f'{TODIR}/')
assert_exists(TODIR / 'd1' / 'd2' / 'k.keepme', label='--include-from kept')
assert_not_exists(TODIR / 'd1' / 'd2' / 'f2', label='--include-from excluded rest')

print("files-from-depth: --files-from/--from0/--exclude-from/--include-from at depth")

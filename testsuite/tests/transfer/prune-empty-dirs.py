#!/usr/bin/env python3

from harness.rsync import (
    FROMDIR, TODIR,
    assert_not_exists, assert_same, makepath, rmtree, run_rsync,
)

src = FROMDIR

def reseed():
    rmtree(src)
    rmtree(TODIR)

reseed()
makepath(src / 'empty' / 'e1' / 'e2', src / 'full' / 'd1' / 'd2')
(src / 'full' / 'd1' / 'd2' / 'file').write_text("data\n")

run_rsync('-a', '-m', f'{src}/', f'{TODIR}/')
assert_not_exists(TODIR / 'empty', label='-m pruned an empty chain')
assert_same(TODIR / 'full' / 'd1' / 'd2' / 'file',
            src / 'full' / 'd1' / 'd2' / 'file', label='-m kept populated chain')

reseed()
makepath(src / 'mixed' / 'sub', src / 'onlylogs' / 'sub')
(src / 'mixed' / 'sub' / 'keep.txt').write_text("k\n")
(src / 'mixed' / 'sub' / 'drop.log').write_text("d\n")
(src / 'onlylogs' / 'sub' / 'a.log').write_text("a\n")

run_rsync('-a', '-m', '--exclude=*.log', f'{src}/', f'{TODIR}/')
assert_same(TODIR / 'mixed' / 'sub' / 'keep.txt',
            src / 'mixed' / 'sub' / 'keep.txt', label='-m kept non-empty dir')
assert_not_exists(TODIR / 'onlylogs',
                  label='-m pruned a dir emptied by an exclude')

print("prune-empty-dirs: empty and filter-emptied chains pruned, populated kept")

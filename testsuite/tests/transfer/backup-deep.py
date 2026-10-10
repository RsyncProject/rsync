#!/usr/bin/env python3

import os

from harness.rsync import (
    FROMDIR, SCRATCHDIR, TODIR,
    assert_not_exists, assert_same, make_tree, rmtree, run_rsync, test_fail,
    walk_files,
)

src = FROMDIR
bak = SCRATCHDIR / 'backups'

def seed():
    rmtree(src)
    rmtree(TODIR)
    rmtree(bak)
    make_tree(src, depth=3, data=True, data_size=4096)
    rels = [p.relative_to(src) for p in walk_files(src)]
    run_rsync('-a', f'{src}/', f'{TODIR}/')
    old = {rel: (src / rel).read_bytes() for rel in rels}
    for rel in rels:
        with open(src / rel, 'ab') as f:
            f.write(b'\nversion-2 tail\n')
    return rels, old

rels, old = seed()
run_rsync('-a', '-b', '--suffix=.old', '--no-whole-file',
          f'{src}/', f'{TODIR}/')
for rel in rels:
    assert_same(TODIR / rel, src / rel, label=f'suffix new {rel}')
    backup = (TODIR / rel)
    backup = backup.with_name(backup.name + '.old')
    if not backup.is_file():
        test_fail(f"--suffix backup missing for {rel}: {backup}")
    if backup.read_bytes() != old[rel]:
        test_fail(f"--suffix backup of {rel} does not hold the old content")

rels, old = seed()
run_rsync('-a', '-b', f'--backup-dir={bak}', '--no-whole-file',
          f'{src}/', f'{TODIR}/')
for rel in rels:
    assert_same(TODIR / rel, src / rel, label=f'backup-dir new {rel}')
    saved = bak / rel
    if not saved.is_file():
        test_fail(f"--backup-dir did not preserve deep path for {rel}: {saved}")
    if saved.read_bytes() != old[rel]:
        test_fail(f"--backup-dir copy of {rel} does not hold the old content")

rels, old = seed()
extra = os.path.join('d1', 'd2', 'd3', 'goner')
(TODIR / extra).write_bytes(b'about to be deleted\n')
run_rsync('-a', '--delete', '-b', f'--backup-dir={bak}', '--no-whole-file',
          f'{src}/', f'{TODIR}/')
assert_not_exists(TODIR / extra, label='deleted file removed from dest')
saved = bak / extra
if not saved.is_file():
    test_fail(f"--backup-dir did not capture the deletion of {extra}")
if saved.read_bytes() != b'about to be deleted\n':
    test_fail("captured deletion has the wrong content")

print("backup-deep: suffix / backup-dir / delete-capture verified at depth")

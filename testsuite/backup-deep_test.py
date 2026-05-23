#!/usr/bin/env python3
"""Property-level coverage of --backup / --suffix / --backup-dir at depth.

backup_test.py is the ported regression test (2 levels, no custom suffix); this
companion checks the concrete outcome of each backup mode at >=3 levels and,
for --backup-dir, with the backup tree placed OUTSIDE the destination (a
sibling) so the old file is renamed across directories into a parallel deep
path -- the cross-directory operation the resolver restructure rewrites.

Asserts, at every level of the tree:
  * --backup --suffix=S  saves the overwritten file as <name>S beside the new
    one (old content in the backup, new content in place);
  * --backup --backup-dir=DIR  relocates the old file to DIR/<relpath>,
    preserving the deep structure, while the destination gets the new content;
  * --backup-dir together with --delete  routes a deletion into the backup
    tree instead of losing it.
"""

import os

from rsyncfns import (
    FROMDIR, SCRATCHDIR, TODIR,
    assert_not_exists, assert_same, make_tree, rmtree, run_rsync, test_fail,
    walk_files,
)

src = FROMDIR
bak = SCRATCHDIR / 'backups'   # sibling of from/ and to/ -- outside both trees


def seed():
    """Fresh v1 source, a matching destination, and the old (v1) bytes; then
    mutate the source to v2 so the next sync overwrites every file."""
    rmtree(src)
    rmtree(TODIR)
    rmtree(bak)
    make_tree(src, depth=3, data=True, data_size=4096)
    rels = [p.relative_to(src) for p in walk_files(src)]
    run_rsync('-a', f'{src}/', f'{TODIR}/')
    old = {rel: (src / rel).read_bytes() for rel in rels}
    for rel in rels:                       # v1 -> v2
        with open(src / rel, 'ab') as f:
            f.write(b'\nversion-2 tail\n')
    return rels, old


# --- --backup --suffix=.old (same directory) --------------------------------
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

# --- --backup-dir at depth, outside the dest tree (cross-dir) ---------------
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

# --- --backup-dir captures a deletion under --delete ------------------------
rels, old = seed()
# Add a deep file to the destination that is absent from the source.
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

#!/usr/bin/env python3

from harness.rsync import (
    FROMDIR, TODIR,
    assert_exists, assert_not_exists, assert_same, make_tree, makepath, rmtree,
    run_rsync, test_fail, walk_files,
)

src = FROMDIR

def seed_src():
    rmtree(src)
    make_tree(src, depth=3)
    return [p.relative_to(src) for p in walk_files(src)]

def fresh_dest():
    rmtree(TODIR)
    run_rsync('-a', f'{src}/', f'{TODIR}/')

rels = seed_src()
fresh_dest()
makepath(TODIR / 'd1' / 'd2' / 'extra')
(TODIR / 'd1' / 'd2' / 'extra' / 'junk').write_text("x\n")
(TODIR / 'd1' / 'd2' / 'orphan').write_text("y\n")
run_rsync('-a', '--delete', f'{src}/', f'{TODIR}/')
assert_not_exists(TODIR / 'd1' / 'd2' / 'extra', label='--delete deep dir')
assert_not_exists(TODIR / 'd1' / 'd2' / 'orphan', label='--delete deep file')
for rel in rels:
    assert_same(TODIR / rel, src / rel, label=f'--delete kept {rel}')

for variant in ('--delete-before', '--delete-during', '--delete-delay',
                '--delete-after'):
    fresh_dest()
    (TODIR / 'd1' / 'd2' / 'gone.txt').write_text("z\n")
    run_rsync('-a', variant, f'{src}/', f'{TODIR}/')
    assert_not_exists(TODIR / 'd1' / 'd2' / 'gone.txt', label=f'{variant} deep')

fresh_dest()
prefix = 'D' * 80
for index in range(300):
    (TODIR / f'{prefix}{index:04d}').write_text('extra\n')
run_rsync('-a', '--delete-delay', f'{src}/', f'{TODIR}/')
if any(path.name.startswith(prefix) for path in TODIR.iterdir()):
    test_fail('--delete-delay left entries after spilling its deletion list')

fresh_dest()
for i in range(5):
    (TODIR / f'extra{i}').write_text("e\n")
run_rsync('-a', '--delete', '--max-delete=2', f'{src}/', f'{TODIR}/',
          check=False)
remaining = list(TODIR.glob('extra*'))
if len(remaining) != 3:
    test_fail(f"--max-delete=2 should leave 3 of 5 extras, found "
              f"{len(remaining)}: {remaining}")

seed_src()
rmtree(TODIR)
makepath(TODIR / 'd1')
(TODIR / 'd1' / 'f1').write_text("old\n")
run_rsync('-a', '--existing', f'{src}/', f'{TODIR}/')
assert_same(TODIR / 'd1' / 'f1', src / 'd1' / 'f1',
            label='--existing updated existing deep file')
assert_not_exists(TODIR / 'f0', label='--existing did not create new top file')
assert_not_exists(TODIR / 'd1' / 'd2', label='--existing did not create new dir')

seed_src()
rmtree(TODIR)
makepath(TODIR / 'd1')
(TODIR / 'd1' / 'f1').write_text("KEEP THIS\n")
run_rsync('-a', '--ignore-existing', f'{src}/', f'{TODIR}/')
if (TODIR / 'd1' / 'f1').read_text() != "KEEP THIS\n":
    test_fail("--ignore-existing overwrote an existing deep file")
assert_same(TODIR / 'f0', src / 'f0', label='--ignore-existing created new file')

rels = seed_src()
fresh_dest()
d = TODIR / 'd1' / 'd2'
(d / 'plain').write_text("extraneous\n")
(d / 'stale~').write_text("already a backup\n")
run_rsync('-a', '-b', '--delete', '--filter=R *~', f'{src}/', f'{TODIR}/')
assert_not_exists(d / 'plain', label='--backup --delete removed extraneous')
assert_exists(d / 'plain~', label='--backup backed up the extraneous file')
assert_not_exists(d / 'stale~', label='--backup --delete removed suffixed orphan')
assert_not_exists(d / 'stale~~',
                  label='is_backup_file: already-suffixed file unlinked, not re-backed-up')
for rel in rels:
    assert_same(TODIR / rel, src / rel, label=f'--backup --delete kept {rel}')

print("delete-deep: delete family, max-delete, existing/ignore-existing, "
      "backup-delete at depth")

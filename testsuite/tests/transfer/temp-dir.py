#!/usr/bin/env python3

from harness.rsync import (
    FROMDIR, SCRATCHDIR, TODIR,
    assert_same, make_tree, rmtree, run_rsync, test_fail, walk_files,
)

src = FROMDIR
tmp = SCRATCHDIR / 'scratch-temp'
rmtree(src)
rmtree(TODIR)
rmtree(tmp)
tmp.mkdir()

make_tree(src, depth=3, data=True)
rels = [p.relative_to(src) for p in walk_files(src)]

run_rsync('-a', f'--temp-dir={tmp}', f'{src}/', f'{TODIR}/')

for rel in rels:
    assert_same(TODIR / rel, src / rel, label=f'temp-dir {rel}')

leftover = sorted(p for p in tmp.rglob('*'))
if leftover:
    test_fail(f"--temp-dir left scratch files behind: {leftover}")

strays = [p for p in TODIR.rglob('.*') if p.is_file()]
if strays:
    test_fail(f"dest tree contains stray temp files: {strays}")

rmtree(TODIR)
proc = run_rsync('-a', f'--temp-dir={SCRATCHDIR}/does-not-exist',
                 f'{src}/', f'{TODIR}/', check=False)
if proc.returncode == 0:
    test_fail("--temp-dir pointing at a missing directory unexpectedly "
              "succeeded")

print("temp-dir: cross-dir rename at depth verified; missing temp dir fails")

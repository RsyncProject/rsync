#!/usr/bin/env python3

from datetime import datetime, timedelta

from harness.rsync import (
    FROMDIR, TODIR,
    assert_same, make_tree, rmtree, run_rsync, test_fail, walk_files,
)

src = FROMDIR
rmtree(src)
make_tree(src, depth=2)
rels = [p.relative_to(src) for p in walk_files(src)]

future = (datetime.now() + timedelta(days=1)).strftime('%Y-%m-%dT%H:%M')
rmtree(TODIR)
run_rsync('-a', f'--stop-at={future}', f'{src}/', f'{TODIR}/')
for rel in rels:
    assert_same(TODIR / rel, src / rel, label=f'--stop-at future {rel}')

rmtree(TODIR)
proc = run_rsync('-a', '--stop-at=2000-01-01T00:00', f'{src}/', f'{TODIR}/',
                 check=False)
if proc.returncode == 0:
    test_fail("--stop-at with a past time was not rejected")

rmtree(TODIR)
run_rsync('-a', '--stop-after=60', f'{src}/', f'{TODIR}/')
for rel in rels:
    assert_same(TODIR / rel, src / rel, label=f'--stop-after {rel}')

print("stop-time: --stop-at future/past and --stop-after verified")

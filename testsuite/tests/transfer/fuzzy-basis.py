#!/usr/bin/env python3

import os

from harness.rsync import (
    FROMDIR, TODIR,
    assert_same, make_data_file, makepath, rmtree, run_rsync, test_fail,
)

src = FROMDIR
deepdir = os.path.join('d1', 'd2')
newfile = os.path.join(deepdir, 'archive-v2.tar')

rmtree(src)
rmtree(TODIR)
makepath(src / deepdir, TODIR / deepdir)

make_data_file(src / newfile, 300_000)
base = (src / newfile).read_bytes()

(TODIR / deepdir / 'archive-v1.tar').write_bytes(base[:280_000] + b'older tail data')
(TODIR / deepdir / 'archive-old.tar').write_bytes(base[:200_000])
(TODIR / deepdir / 'unrelated.dat').write_bytes(b'nothing alike' * 1000)

proc = run_rsync('-a', '--fuzzy', '--no-whole-file', '--debug=FUZZY',
                 f'{src}/', f'{TODIR}/', capture_output=True)
want = f'fuzzy basis selected for {newfile}: {os.path.join(deepdir, "archive-v1.tar")}'
if want not in proc.stdout:
    test_fail(f"--fuzzy did not score archive-v1.tar as the closest basis; "
              f"expected {want!r}, --debug=FUZZY output was:\n{proc.stdout}")
assert_same(TODIR / newfile, src / newfile, label='fuzzy result')

print("fuzzy-basis: --fuzzy candidate scoring (fuzzy_distance) verified at depth")

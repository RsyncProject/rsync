#!/usr/bin/env python3
"""Coverage of --fuzzy basis selection scoring (util1.c fuzzy_distance).

When the destination has no exact match for a source file, --fuzzy makes the
generator score the same-directory candidates by name similarity (fuzzy_distance)
and use the closest as the delta basis. Set this up at depth with several
similar-named candidates so the scorer actually runs.
"""

import os

from rsyncfns import (
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

# Destination has NO 'archive-v2.tar', but several similar-named candidates that
# are mostly identical to it -- so fuzzy must score them by name distance.
(TODIR / deepdir / 'archive-v1.tar').write_bytes(base[:280_000] + b'older tail data')
(TODIR / deepdir / 'archive-old.tar').write_bytes(base[:200_000])
(TODIR / deepdir / 'unrelated.dat').write_bytes(b'nothing alike' * 1000)

# Capture --debug=FUZZY: a correct final file alone would also result from a
# full transfer that ignored --fuzzy, so assert the scorer actually chose the
# closest-named candidate (archive-v1.tar) as the basis, not just that bytes match.
proc = run_rsync('-a', '--fuzzy', '--no-whole-file', '--debug=FUZZY',
                 f'{src}/', f'{TODIR}/', capture_output=True)
want = f'fuzzy basis selected for {newfile}: {os.path.join(deepdir, "archive-v1.tar")}'
if want not in proc.stdout:
    test_fail(f"--fuzzy did not score archive-v1.tar as the closest basis; "
              f"expected {want!r}, --debug=FUZZY output was:\n{proc.stdout}")
assert_same(TODIR / newfile, src / newfile, label='fuzzy result')

print("fuzzy-basis: --fuzzy candidate scoring (fuzzy_distance) verified at depth")

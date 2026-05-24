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
    assert_same, make_data_file, makepath, rmtree, run_rsync,
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

run_rsync('-a', '--fuzzy', '--no-whole-file', f'{src}/', f'{TODIR}/')
assert_same(TODIR / newfile, src / newfile, label='fuzzy result')

print("fuzzy-basis: --fuzzy candidate scoring (fuzzy_distance) verified at depth")

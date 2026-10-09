#!/usr/bin/env python3
"""Coverage of --temp-dir (-T) at depth and across directory boundaries.

--temp-dir tells the receiver to create its scratch/temp copies in DIR rather
than in the destination directory, then rename the finished file into place.
That rename crosses from the temp directory to a destination directory several
levels deep -- exactly the two-directory operation the path-resolution
restructure rewrites -- so it must be guarded at depth with the temp dir kept
OUTSIDE the destination tree.

Asserts:
  * a transfer with --temp-dir pointing outside the dest tree reproduces the
    source byte-for-byte at every level;
  * no temp/scratch files are left behind in the temp dir or the dest tree;
  * a non-existent --temp-dir makes the receiver fail (so we know the option
    is actually consulted, not silently ignored).
"""

import os

from rsyncfns import (
    FROMDIR, SCRATCHDIR, TODIR,
    assert_same, make_tree, rmtree, run_rsync, test_fail, walk_files,
)

src = FROMDIR
tmp = SCRATCHDIR / 'scratch-temp'   # sibling of from/ and to/ -- outside both
rmtree(src)
rmtree(TODIR)
rmtree(tmp)
tmp.mkdir()

make_tree(src, depth=3, data=True)
rels = [p.relative_to(src) for p in walk_files(src)]

# Transfer with the temp dir outside the destination tree.
run_rsync('-a', f'--temp-dir={tmp}', f'{src}/', f'{TODIR}/')

for rel in rels:
    assert_same(TODIR / rel, src / rel, label=f'temp-dir {rel}')

# The temp dir must be clean afterwards (every scratch file renamed away).
leftover = sorted(p for p in tmp.rglob('*'))
if leftover:
    test_fail(f"--temp-dir left scratch files behind: {leftover}")

# No stray rsync temp files (.name.XXXXXX) anywhere in the dest tree.
strays = [p for p in TODIR.rglob('.*') if p.is_file()]
if strays:
    test_fail(f"dest tree contains stray temp files: {strays}")

# Negative: a missing temp dir must cause a receiver failure, proving the
# option is honoured rather than ignored.
rmtree(TODIR)
proc = run_rsync('-a', f'--temp-dir={SCRATCHDIR}/does-not-exist',
                 f'{src}/', f'{TODIR}/', check=False)
if proc.returncode == 0:
    test_fail("--temp-dir pointing at a missing directory unexpectedly "
              "succeeded")

print("temp-dir: cross-dir rename at depth verified; missing temp dir fails")

#!/usr/bin/env python3
"""Coverage of -X (xattrs) at depth.

xattrs_test.py exercises a shallow tree; this companion sets a distinctive user
xattr on a file AND a directory at every level of a >=3-deep tree and checks
that -X reproduces them all (the xattr is applied per entry, on a name whose
parent chain the resolver restructure rewrites).
"""

import os
import sys

from rsyncfns import (
    FROMDIR, TODIR,
    make_tree, rmtree, run_rsync, test_fail, test_skipped,
    walk_dirs, walk_files, xattr_dump, xattr_set, xattrs_supported,
)

if not xattrs_supported():
    test_skipped("rsync built without xattr support (or no xattr tooling here)")

src = FROMDIR
rmtree(src)
rmtree(TODIR)
make_tree(src, depth=3)

entries = [p.relative_to(src) for p in (walk_dirs(src) + walk_files(src))]
entries.sort()

os.chdir(src)
try:
    for i, rel in enumerate(entries):
        xattr_set('depth', f'value-{i}-{rel}', str(rel))
except OSError as e:
    test_skipped(f"unable to set an xattr on this filesystem: {e}")

want = xattr_dump(*[str(r) for r in entries])

run_rsync('-aX', '-f-x_system.*', '-f-x_security.*', '--super',
          f'{src}/', f'{TODIR}/')

os.chdir(TODIR)
got = xattr_dump(*[str(r) for r in entries])

if got != want:
    from difflib import unified_diff
    sys.stdout.write(''.join(unified_diff(
        want.splitlines(keepends=True), got.splitlines(keepends=True),
        fromfile='source', tofile='dest')))
    test_fail("xattrs differ between source and destination at depth")

print("xattrs-depth: -X reproduced a user xattr on every entry at depth")

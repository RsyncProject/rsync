#!/usr/bin/env python3

import os
import sys

from harness.environment import FROMDIR, TODIR
from harness.filesystem import make_tree, rmtree, walk_dirs, walk_files
from harness.results import test_fail, test_skipped
from harness.rsync import run_rsync
from harness.xattrs import dump_values as xattr_dump, set_value as xattr_set, is_supported as xattrs_supported
from harness import metadata

metadata(features={'xattr_runtime'}, transports={'pipe'})

if not xattrs_supported():
    test_skipped("rsync built without xattr support (or no xattr tooling here)",
                 capability='xattr_runtime')

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
    test_skipped(f"unable to set an xattr on this filesystem: {e}",
                 capability='xattr_runtime')

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

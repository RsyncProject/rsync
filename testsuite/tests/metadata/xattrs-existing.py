#!/usr/bin/env python3

import os
import platform

from harness import metadata
from harness.environment import FROMDIR, TODIR
from harness.filesystem import makepath
from harness.results import test_fail, test_skipped
from harness.rsync import run_rsync
from harness.xattrs import get_value, is_supported, set_value

metadata(features={'xattr_runtime'}, transports={'pipe'}, tags={'metadata', 'regression'})

if not is_supported():
    test_skipped('rsync has no usable xattr support', capability='xattr_runtime')

makepath(FROMDIR, TODIR)
original = 'original'
neighbour = 'a neighbouring value long enough to be abbreviated'
mtime = 946684800  # 2000-01-01T00:00:00Z
for name, value in (('same-size', 'modified'), ('shrink', 'small'), ('grow', 'a longer replacement')):
    source, target = FROMDIR / name, TODIR / name
    for path, attr in ((source, value), (target, original)):
        path.write_text('unchanged file data\n')
        set_value('first', attr, path)
        set_value('second', neighbour, path)
        os.utime(path, (mtime, mtime))
    result = run_rsync('-aX', str(source), str(target), check=False, capture_output=True)
    if name == 'grow' and platform.system() == 'NetBSD':
        if result.returncode != 23:
            test_fail(f'NetBSD xattr growth must be refused: {result.returncode}: {result.stderr}')
        value = original
    elif result.returncode:
        test_fail(f'{name}: rsync exited {result.returncode}: {result.stderr}')
    if get_value('first', target) != value.encode() or get_value('second', target) != neighbour.encode():
        test_fail(f'{name}: rsync did not preserve the expected xattrs')

#!/usr/bin/env python3
"""Windows path spellings.

"C:\\dir" must be read as a drive path rather than as HOST:PATH (see
IS_DRIVE_PATH in win32/win32compat.h), backslash separators must be accepted
wherever a slash is, and a drive-letter path counts as absolute
(IS_ABS_PATH).

Copyright (C) 2026 Max Vilimpoc, rsync CMake/Windows port.
Distributed under the same GPL-3.0-or-later terms as the rest of rsync.
"""

import os

from wintest import (FROM, SCRATCH, check, compare_trees, ok, rsync, skip,
                     write)

if os.name != 'nt':
    skip('drive-letter paths are a Windows spelling')

write(FROM / 'one.txt', 'first\n')
write(FROM / 'sub' / 'two.txt', 'second\n')

# Backslashes on both sides.
dst = SCRATCH / 'backslash'
rsync('-rt', f'{FROM}\\', f'{dst}\\')
compare_trees(FROM, dst, 'copy written with backslashes')

# Forward slashes with a drive letter.
dst = SCRATCH / 'forward'
rsync('-rt', str(FROM).replace('\\', '/') + '/',
      str(dst).replace('\\', '/') + '/')
compare_trees(FROM, dst, 'copy written with forward slashes')

# Mixed separators in one argument.
dst = SCRATCH / 'mixed'
mixed_src = str(FROM).replace('\\', '/', 1) + '\\'
rsync('-rt', mixed_src, f'{dst}/')
compare_trees(FROM, dst, 'copy written with mixed separators')

# No trailing slash: the source directory itself lands inside the target.
dst = SCRATCH / 'notrail'
dst.mkdir()
rsync('-rt', str(FROM), f'{dst}/')
check((dst / FROM.name / 'one.txt').is_file(),
      'a source without a trailing slash did not nest under the destination')

# A single-letter host is still a host when followed by a relative path, so
# this must be treated as remote and fail rather than silently copying.
proc = rsync('-rt', 'h:relative/path', str(SCRATCH / 'nowhere'),
             expect=None)
check(proc.returncode != 0,
      "'h:relative/path' was treated as a local path; it should be a host spec")

ok('drive letters, separators and trailing slashes all handled')

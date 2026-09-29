#!/usr/bin/env python3
"""Local-to-local copying, the path that re-executes rsync as its own
--server (win32/win32pipe.c).

Covers a first copy, an incremental no-op re-run, and --delete removing a
file the source no longer has.

Copyright (C) 2026 Max Vilimpoc, rsync CMake/Windows port.
Distributed under the same GPL-3.0-or-later terms as the rest of rsync.
"""

from wintest import (FROM, TO, check, compare_trees, make_test_tree, ok,
                     rsync, write)

make_test_tree(FROM)

# First copy.
rsync('-rt', f'{FROM}/', f'{TO}/')
compare_trees(FROM, TO, 'source and destination')

# An unchanged re-run must transfer nothing.
proc = rsync('-rt', '--stats', f'{FROM}/', f'{TO}/')
for line in proc.stdout.splitlines():
    if 'regular files transferred' in line:
        count = line.rsplit(':', 1)[1].strip().replace(',', '')
        check(count == '0',
              f'incremental re-run transferred {count} files, expected 0')
        break
else:
    check(False, '--stats did not report a transfer count')

# A new file appears; only it should move, and the trees must match after.
write(FROM / 'sub' / 'added.txt', 'added later\n')
rsync('-rt', f'{FROM}/', f'{TO}/')
compare_trees(FROM, TO, 'after adding a file')

# --delete must remove what the source dropped, including in subdirs.
(FROM / 'plain.txt').unlink()
(FROM / 'sub' / 'nested.txt').unlink()
rsync('-rt', '--delete', f'{FROM}/', f'{TO}/')
compare_trees(FROM, TO, 'after --delete')
check(not (TO / 'plain.txt').exists(), '--delete left plain.txt behind')
check(not (TO / 'sub' / 'nested.txt').exists(),
      '--delete left sub/nested.txt behind')

ok('local copy, incremental and --delete all correct')

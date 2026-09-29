#!/usr/bin/env python3
"""rsync's wildmatch unit tests.

The wildtest helper runs every case in wildtest.txt through wildmatch(),
which is pure string matching with no OS dependency -- so this is one of
rsync's own unit tests running unmodified on Windows.  The option sets are
those in testsuite/wildmatch_test.py: -x selects a wildmatch_join() variant
and -e the "empty component" handling.

Copyright (C) 2026 Max Vilimpoc, rsync CMake/Windows port.
Distributed under the same GPL-3.0-or-later terms as the rest of rsync.
"""

from wintest import SRCDIR, check, helper, ok

OPTION_SETS = [
    [],
    ['-x1'],
    ['-x1', '-e1'],
    ['-x1', '-e1se'],
    ['-x2'],
    ['-x2', '-ese'],
    ['-x3'],
    ['-x3', '-e1'],
    ['-x4'],
    ['-x4', '-e2e'],
    ['-x5'],
    ['-x5', '-es'],
]

EXPECTED = 'No wildmatch errors found.\n'

cases = SRCDIR / 'wildtest.txt'
check(cases.is_file(), f'missing test data: {cases}')

for opts in OPTION_SETS:
    label = ' '.join(opts) or '(no options)'
    proc = helper('wildtest', *opts, cases)
    check(proc.stdout == EXPECTED,
          f'wildtest {label} reported:\n{proc.stdout}')
    print(f'wildtest {label}: clean')

ok(f'{len(OPTION_SETS)} wildmatch option sets clean')

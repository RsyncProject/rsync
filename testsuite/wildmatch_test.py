#!/usr/bin/env python3
# Python rewrite of testsuite/wildmatch.test.
#
# Exercise the wildmatch() function (with no options) and wildmatch_join()
# (via -x and/or -e) by running the wildtest helper across a variety of
# option combinations and confirming each reports no errors.

import subprocess

from rsyncfns import SRCDIR, TOOLDIR, test_fail


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

EXPECTED = "No wildmatch errors found.\n"

wildtest = str(TOOLDIR / 'wildtest')
wildtest_txt = str(SRCDIR / 'wildtest.txt')

for opts in OPTION_SETS:
    print(f"Running wildtest with {' '.join(opts)}")
    proc = subprocess.run(
        [wildtest, *opts, wildtest_txt],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        test_fail(
            f"wildtest {' '.join(opts)} exited {proc.returncode}\n"
            f"stderr:\n{proc.stderr}\nstdout:\n{proc.stdout}"
        )
    if proc.stdout != EXPECTED:
        test_fail(
            f"wildtest {' '.join(opts)} output did not match expected.\n"
            f"--- expected ---\n{EXPECTED}"
            f"--- got ---\n{proc.stdout}"
        )

#!/usr/bin/env python3
# Python rewrite of testsuite/simd-checksum.test.
#
# Run the simdtest helper, which compares the SIMD checksum
# implementations against the C reference. Skip if the helper wasn't
# built (i.e. SIMD acceleration is unavailable on this host).

import os
import subprocess

from rsyncfns import TOOLDIR, test_fail, test_skipped


simdtest = TOOLDIR / 'simdtest'
if not (simdtest.is_file() and os.access(simdtest, os.X_OK)):
    test_skipped("simdtest not built (SIMD not available)")

proc = subprocess.run([str(simdtest)])
if proc.returncode != 0:
    test_fail(f"simdtest exited {proc.returncode}")

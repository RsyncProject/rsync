#!/usr/bin/env python3

import os
import subprocess

from harness.rsync import TOOLDIR, test_fail, test_skipped

simdtest = TOOLDIR / 'simdtest'
if not (simdtest.is_file() and os.access(simdtest, os.X_OK)):
    test_skipped("simdtest not built (SIMD not available)", capability='simd')

proc = subprocess.run([str(simdtest)])
if proc.returncode != 0:
    test_fail(f"simdtest exited {proc.returncode}")

#!/usr/bin/env python3

import os
import subprocess

from harness.environment import TOOLDIR
from harness.results import test_fail, test_skipped
from harness import metadata

metadata(features={'simd'}, transports={'pipe'})

simdtest = TOOLDIR / 'simdtest'
if not (simdtest.is_file() and os.access(simdtest, os.X_OK)):
    test_skipped("simdtest not built (SIMD not available)", capability='simd')

proc = subprocess.run([str(simdtest)])
if proc.returncode != 0:
    test_fail(f"simdtest exited {proc.returncode}")

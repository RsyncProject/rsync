#!/usr/bin/env python3

import subprocess

from harness import metadata
from harness.environment import TOOLDIR
from harness.results import test_fail

metadata(transports={'pipe'})

helper = TOOLDIR / 't_byteorder'
if not helper.is_file():
    test_fail('Missing test helper: t_byteorder')
result = subprocess.run([str(helper)], capture_output=True, text=True, timeout=30)
if result.returncode:
    test_fail(f'Byte-order check returned {result.returncode}:\n{result.stderr}')

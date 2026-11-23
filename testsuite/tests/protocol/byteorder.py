#!/usr/bin/env python3

import subprocess

from harness import metadata
from harness.environment import TOOLDIR
from harness.results import test_fail

metadata(transports={'pipe'})

for name in ('t_byteorder', 't_byteorder_portable'):
    helper = TOOLDIR / name
    if not helper.is_file():
        test_fail(f'Missing test helper: {name}')
    result = subprocess.run([str(helper)], capture_output=True, text=True, timeout=30)
    if result.returncode:
        test_fail(f'{name} returned {result.returncode}:\n{result.stderr}')

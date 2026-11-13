#!/usr/bin/env python3

import subprocess

from harness.environment import TOOLDIR
from harness.results import test_fail
from harness import metadata

metadata(transports={'pipe'})

RERR_MALLOC = 22

helper = TOOLDIR / 't_hashtable_overflow'
if not helper.is_file():
    test_fail('Missing test helper: t_hashtable_overflow')

proc = subprocess.run([str(helper)], capture_output=True, text=True, timeout=30)
if proc.returncode != RERR_MALLOC:
    test_fail(f'oversized hashtable returned {proc.returncode}, expected {RERR_MALLOC}:\n{proc.stderr}')

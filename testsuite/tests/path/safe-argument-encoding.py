#!/usr/bin/env python3

import subprocess

from harness.environment import TOOLDIR
from harness.results import test_fail, test_skipped
from harness import metadata

metadata(transports={'pipe'})

helper = TOOLDIR / 't_safe_arg'
if not helper.is_file():
    test_skipped("t_safe_arg helper not built")

proc = subprocess.run([str(helper)], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                      timeout=30)
if proc.returncode != 0:
    test_fail("safe argument encoding read an uninitialised byte\n"
              + (proc.stdout or b'').decode('utf-8', 'replace')
              + (proc.stderr or b'').decode('utf-8', 'replace'))
print((proc.stdout or b'').decode('utf-8', 'replace').strip())

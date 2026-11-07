#!/usr/bin/env python3

import subprocess

from harness.rsync import TOOLDIR, test_fail, test_skipped

helper = TOOLDIR / 't_safe_arg'
if not helper.is_file():
    test_skipped("t_safe_arg helper not built")

proc = subprocess.run([str(helper)], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                      timeout=30)
if proc.returncode != 0:
    test_fail("safe_arg leaks an uninitialized byte (counter/writer miscount)\n"
              + (proc.stdout or b'').decode('utf-8', 'replace')
              + (proc.stderr or b'').decode('utf-8', 'replace'))
print((proc.stdout or b'').decode('utf-8', 'replace').strip())

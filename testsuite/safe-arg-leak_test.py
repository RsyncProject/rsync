#!/usr/bin/env python3
# Regression test for KI-54: safe_arg() in filename mode (opt==NULL) must not leak
# an uninitialized heap byte.  The escape counter and the writer disagreed on a
# backslash before a wildcard / a trailing backslash (the strchr(WILD_CHARS,'\0')
# NUL footgun), leaving a gap that strlen() walked into.  The t_safe_arg helper
# links the real options.o, poisons the heap, and checks the quoting is exact.

import subprocess

from rsyncfns import TOOLDIR, test_fail, test_skipped

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

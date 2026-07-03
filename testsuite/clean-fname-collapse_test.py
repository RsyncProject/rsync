#!/usr/bin/env python3
# Regression test for KI-50: clean_fname(name, CFN_COLLAPSE_DOT_DOT_DIRS) must
# collapse ".." components.  An off-by-one (*s=='/' should be s[-1]=='/'; s+1
# should be s) left the collapse dead for every multi-component/absolute path.
# The t_clean_fname helper links the real util1.o and checks the collapse.

import subprocess

from rsyncfns import TOOLDIR, test_fail, test_skipped

helper = TOOLDIR / 't_clean_fname'
if not helper.is_file():
    test_skipped("t_clean_fname helper not built")

proc = subprocess.run([str(helper)], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                      timeout=30)
if proc.returncode != 0:
    test_fail("clean_fname CFN_COLLAPSE_DOT_DOT_DIRS off-by-one (collapse dead)\n"
              + (proc.stdout or b'').decode('utf-8', 'replace')
              + (proc.stderr or b'').decode('utf-8', 'replace'))
print((proc.stdout or b'').decode('utf-8', 'replace').strip())

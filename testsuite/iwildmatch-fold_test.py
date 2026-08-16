#!/usr/bin/env python3
# Regression test for KI-53: iwildmatch() must fold case on BOTH the text and the
# pattern.  The old dowild() folded only the text char, so an upper-case pattern
# token (e.g. a daemon "hosts deny = *.BADDOMAIN.COM") failed to match a
# lower-case host -> access-control fail-open.  The t_iwildmatch helper links the
# real lib/wildmatch.o and exercises the fold both ways.

import subprocess

from rsyncfns import TOOLDIR, test_fail, test_skipped

helper = TOOLDIR / 't_iwildmatch'
if not helper.is_file():
    test_skipped("t_iwildmatch helper not built")

proc = subprocess.run([str(helper)], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                      timeout=30)
if proc.returncode != 0:
    test_fail("iwildmatch case-fold is asymmetric (pattern not folded)\n"
              + (proc.stdout or b'').decode('utf-8', 'replace')
              + (proc.stderr or b'').decode('utf-8', 'replace'))
print((proc.stdout or b'').decode('utf-8', 'replace').strip())

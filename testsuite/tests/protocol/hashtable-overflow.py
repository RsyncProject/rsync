#!/usr/bin/env python3

import subprocess

from harness.rsync import TOOLDIR, test_fail, test_skipped

RERR_MALLOC = 22

helper = TOOLDIR / 't_hashtable_overflow'
if not helper.is_file():
    test_skipped("t_hashtable_overflow helper not built")

proc = subprocess.run([str(helper)], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                      timeout=30)
rc = proc.returncode
if rc == RERR_MALLOC:
    print("hashtable-overflow: hashtable_create rejected the oversized size "
          "(exited RERR_MALLOC) instead of under-allocating")
elif rc < 0:
    test_fail(f"t_hashtable_overflow crashed (signal {-rc}): the hashtable "
              "size*node_size integer overflow under-allocated the table\n"
              + (proc.stderr or b'').decode('utf-8', 'replace'))
else:
    test_fail(f"t_hashtable_overflow exited {rc}, expected RERR_MALLOC ({RERR_MALLOC}): "
              "the oversized hashtable_create was not rejected\n"
              + (proc.stderr or b'').decode('utf-8', 'replace'))

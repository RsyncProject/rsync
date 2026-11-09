#!/usr/bin/env python3
# Regression test for the hashtable size*node_size integer overflow (KI-11/12).
#
# hashtable_create() once computed the slot-array byte count in 32-bit int
# (new_array0(char, size * node_size)); a large peer/data-driven size wrapped the
# product to a tiny value, under-allocating the table while tbl->size kept the
# huge size -- a later node access ran out of bounds (heap overflow / SEGV).
#
# The t_hashtable_overflow helper (built by make check) links the real
# hashtable.o, sets a realistic --max-alloc, and asks for an absurd size.  The
# fix passes the factors separately so my_alloc's guard rejects it, exiting
# RERR_MALLOC; a regressed build under-allocates and crashes on the node access.

import subprocess

from rsyncfns import TOOLDIR, test_fail, test_skipped

RERR_MALLOC = 22   # errcode.h

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

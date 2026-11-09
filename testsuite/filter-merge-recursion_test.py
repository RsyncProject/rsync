#!/usr/bin/env python3
# Regression test for unbounded merge-file include recursion in the filter
# parser (exclude.c parse_filter_file <-> parse_filter_str).
#
# A '. file' (merge) directive inside a filter file makes parse_filter_str()
# call parse_filter_file() on the named file, which feeds each of its lines
# back through parse_filter_str(). Nothing bounded the nesting depth, so a
# merge file whose only line re-includes itself recurses until the stack
# guard page is hit -> SIGSEGV.
#
# Threat (code-scanner daemon-protocol fuzzing, June 2026): a malicious client of a
# writable daemon module uploads a '.merge' file containing '. .merge' and
# drives a transfer with --filter=': .merge'; the generator's per-dir merge
# handling in push_local_filters() then re-parses that file forever and the
# forked daemon child crashes. The recursion lives in shared filter code, so
# we reproduce it with a plain local transfer: a per-dir merge rule
# (': .merge') plus a source '.merge' that merges itself.
#
# Oracle:
#   * pre-fix: the sender recurses to stack exhaustion and dies with SIGSEGV
#              (the client sees the peer vanish / a signal exit).
#   * fixed:   parse_filter_file() caps nesting at MAX_MERGE_DEPTH (32) and,
#              on this operator/sender-supplied (XFLG_FATAL_ERRORS) merge,
#              exits cleanly with RERR_FILEIO and a depth-limit diagnostic.

import subprocess

from rsyncfns import SCRATCHDIR, makepath, rmtree, rsync_argv, test_fail

SRC = SCRATCHDIR / 'mergerec-src'
DST = SCRATCHDIR / 'mergerec-dst'
rmtree(SRC)
rmtree(DST)
makepath(SRC, DST)

# A per-dir merge file that includes itself: '. .merge' re-merges .merge.
(SRC / '.merge').write_text(". .merge\n")
(SRC / 'file.txt').write_text("hello\n")

proc = subprocess.run(
    rsync_argv('-a', '--filter=: .merge', f'{SRC}/', f'{DST}/'),
    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

# A process killed by a signal shows up as a negative returncode in Python, or
# as 128+signal when a child died and the parent reported it. RERR_STREAMIO
# (12) with no depth diagnostic means the peer crashed mid-stream.
crashed = (proc.returncode < 0 or proc.returncode >= 128
           or (proc.returncode == 12
               and 'merge-file include depth limit' not in proc.stdout))
if crashed:
    test_fail(
        "filter merge-file recursion was not capped: rsync died abnormally "
        f"(rc={proc.returncode}) on a self-including '.merge'. "
        f"output:\n{proc.stdout}")

if 'merge-file include depth limit' not in proc.stdout:
    test_fail(
        "expected the MAX_MERGE_DEPTH diagnostic; the self-including merge "
        f"file did not hit the cap (rc={proc.returncode}).\n{proc.stdout}")

print("filter-merge-recursion: self-including merge file is capped at "
      "MAX_MERGE_DEPTH (clean RERR_FILEIO, no stack-exhaustion crash).")

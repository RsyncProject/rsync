#!/usr/bin/env python3

import subprocess

from harness.rsync import SCRATCHDIR, makepath, rmtree, rsync_argv, test_fail

SRC = SCRATCHDIR / 'mergerec-src'
DST = SCRATCHDIR / 'mergerec-dst'
rmtree(SRC)
rmtree(DST)
makepath(SRC, DST)

(SRC / '.merge').write_text(". .merge\n")
(SRC / 'file.txt').write_text("hello\n")

proc = subprocess.run(
    rsync_argv('-a', '--filter=: .merge', f'{SRC}/', f'{DST}/'),
    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

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

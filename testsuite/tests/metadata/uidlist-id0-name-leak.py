#!/usr/bin/env python3

import glob
import os
import subprocess

from harness.rsync import (
    FROMDIR, SCRATCHDIR, RSYNC,
    require_asan, rmtree, rsync_argv, test_fail,
)

require_asan("KI-25 id-0 name leak is only observable under AddressSanitizer/LSan", RSYNC)

src = FROMDIR
rmtree(src)
src.mkdir(parents=True)
(src / 'f.txt').write_text("hello\n")

asan_log = SCRATCHDIR / 'id0-leak-asan'
for stale in glob.glob(f"{asan_log}.*"):
    os.unlink(stale)
os.environ['ASAN_OPTIONS'] = (
    f"detect_leaks=1:abort_on_error=0:log_path={asan_log}"
)

p = subprocess.run(rsync_argv('-og', '--list-only', f'{src}/'),
                   capture_output=True, text=True)

if 'f.txt' not in p.stdout:
    test_fail(f"--list-only did not list the source file; leak path not exercised:\n{p.stdout}{p.stderr}")

reports = ''.join(open(r, errors='replace').read()
                  for r in glob.glob(f"{asan_log}.*"))
if 'send_one_list' in reports:
    test_fail("send_one_list leaked the id-0 name string (KI-25):\n"
              + reports[:1500])

print("uidlist-id0-name-leak: send_one_list does not leak the id-0 name")

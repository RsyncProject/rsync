#!/usr/bin/env python3
"""KI-25: send_one_list leaks the id-0 (root) name string.

uidlist.c:send_one_list, when xmit_id0_names is negotiated, passes
uid_to_user(0)/gid_to_group(0) straight to send_one_name(); those return a
strdup'd name (uidlist.c:120/137) whose pointer is never stored or freed.  Up
to two small strings leak per transfer.

ASan/LSan reproducer: run an owner/group-preserving transfer (which sends the
uid+gid lists with the id-0 name) and assert no leak report names send_one_list.
Gated on an AddressSanitizer build.
"""

import glob
import os
import subprocess

from rsyncfns import (
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

# -o/-g make the sender transmit the uid+gid lists; a modern<->modern transfer
# negotiates xmit_id0_names, so send_one_list emits the id-0 name.  --list-only
# makes the client exit via exit() (so LSan runs) rather than the _exit() of the
# normal receiver/generator shutdown that would otherwise hide the leak.
p = subprocess.run(rsync_argv('-og', '--list-only', f'{src}/'),
                   capture_output=True, text=True)

# Non-vacuity: the listing must have actually run (proving send_id_lists ->
# send_one_list executed).  We can't check the exit code: under detect_leaks=1
# rsync's intentional at-exit leaks already force a nonzero status.
if 'f.txt' not in p.stdout:
    test_fail(f"--list-only did not list the source file; leak path not exercised:\n{p.stdout}{p.stderr}")

reports = ''.join(open(r, errors='replace').read()
                  for r in glob.glob(f"{asan_log}.*"))
if 'send_one_list' in reports:
    test_fail("send_one_list leaked the id-0 name string (KI-25):\n"
              + reports[:1500])

print("uidlist-id0-name-leak: send_one_list does not leak the id-0 name")

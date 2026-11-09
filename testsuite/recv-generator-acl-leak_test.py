#!/usr/bin/env python3
"""KI-23: recv_generator leaks real_sx ACL data on the directory path.

In generator.c:recv_generator the directory branch calls set_file_attrs() on
&real_sx, which (under --acls) loads the destination's current ACL into
real_sx via get_acl_fdat(), then does `goto cleanup`.  The cleanup label only
frees sx, never real_sx (real_sx is freed solely on the regular-file path), so
each pre-existing ACL-bearing destination directory leaks its ACL data.

ASan/LSan reproducer: sync --acls onto a destination directory that already
carries an ACL, then assert no leak report names recv_generator.  Gated on an
AddressSanitizer build and on setfacl being available.
"""

import glob
import os
import shutil
import subprocess

from rsyncfns import (
    SCRATCHDIR, RSYNC,
    acls_supported, require_asan, rmtree, rsync_argv, test_fail, test_skipped,
)

require_asan("KI-23 real_sx ACL leak is only observable under AddressSanitizer/LSan", RSYNC)
if not acls_supported():
    test_skipped("rsync built without ACL support, or filesystem rejects ACLs")
if not shutil.which('setfacl'):
    test_skipped("setfacl not available to plant a destination-directory ACL")

base = SCRATCHDIR / 'acl-leak'
rmtree(base)
src = base / 'src'
dst = base / 'dst'
(src / 'sub').mkdir(parents=True)
(src / 'sub' / 'f.txt').write_text("updated-content\n")  # different size -> always transferred
(dst / 'sub').mkdir(parents=True)
(dst / 'sub' / 'f.txt').write_text("old\n")

# Plant an ACL on the pre-existing destination directory so recv_generator's
# directory branch loads it into real_sx (the leaked allocation).
if subprocess.run(['setfacl', '-m', 'u:nobody:rwx', str(dst / 'sub')]).returncode != 0:
    test_skipped("setfacl could not set an ACL on the destination directory")

asan_log = base / 'acl-leak-asan'
for stale in glob.glob(f"{asan_log}.*"):
    os.unlink(stale)
os.environ['ASAN_OPTIONS'] = (
    f"detect_leaks=1:abort_on_error=0:log_path={asan_log}"
)

p = subprocess.run(rsync_argv('-a', '--acls', f'{src}/', f'{dst}/'),
                   capture_output=True, text=True)

# Non-vacuity: the transfer must have actually run through recv_generator's
# directory branch (the dest dir pre-exists with an ACL, and its child file is
# updated).  We can't check the exit code: under detect_leaks=1 rsync's
# intentional at-exit leaks already force a nonzero status.
if (dst / 'sub' / 'f.txt').read_text() != "updated-content\n":
    test_fail(f"--acls transfer did not update the destination; leak path not exercised:\n{p.stderr}")

reports = ''.join(open(r, errors='replace').read()
                  for r in glob.glob(f"{asan_log}.*"))
if 'recv_generator' in reports:
    test_fail("recv_generator leaked real_sx ACL data on the directory path (KI-23):\n"
              + reports[:1500])

print("recv-generator-acl-leak: recv_generator does not leak real_sx ACL data")

#!/usr/bin/env python3

import glob
import os
import shutil
import subprocess

from harness.rsync import (
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
(src / 'sub' / 'f.txt').write_text("updated-content\n")
(dst / 'sub').mkdir(parents=True)
(dst / 'sub' / 'f.txt').write_text("old\n")

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

if (dst / 'sub' / 'f.txt').read_text() != "updated-content\n":
    test_fail(f"--acls transfer did not update the destination; leak path not exercised:\n{p.stderr}")

reports = ''.join(open(r, errors='replace').read()
                  for r in glob.glob(f"{asan_log}.*"))
if 'recv_generator' in reports:
    test_fail("recv_generator leaked real_sx ACL data on the directory path (KI-23):\n"
              + reports[:1500])

print("recv-generator-acl-leak: recv_generator does not leak real_sx ACL data")

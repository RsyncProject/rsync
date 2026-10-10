#!/usr/bin/env python3

import os

from harness.rsync import (
    SCRATCHDIR,
    acls_supported, xattrs_supported, acl_set, xattr_set,
    makepath, rmtree, rsync_argv, test_fail, test_skipped,
)
import subprocess

if not acls_supported():
    test_skipped("ACLs not supported on this filesystem", capability='linux_acl')
if not xattrs_supported():
    test_skipped("xattrs not supported on this filesystem", capability='backup_metadata')

src = SCRATCHDIR / 'bak-src'
dest = SCRATCHDIR / 'bak-dest'
bak = SCRATCHDIR / 'bak-dir'
rmtree(src)
rmtree(dest)
rmtree(bak)
makepath(src / 'd1' / 'd2', dest / 'd1' / 'd2', bak)

(src / 'd1' / 'd2' / 'file').write_bytes(b'NEW')
(dest / 'd1' / 'd2' / 'file').write_bytes(b'OLD')
os.utime(dest / 'd1' / 'd2' / 'file', (1_600_000_000, 1_600_000_000))

for d in (dest / 'd1', dest / 'd1' / 'd2'):
    if not acl_set('u:0:rwx', d):
        test_skipped(f"setfacl failed on {d}", capability='linux_acl')
    xattr_set('user.bak-cache', 'v', d)

r = subprocess.run(
    rsync_argv('-aAX', '--backup', f'--backup-dir={bak}', f'{src}/', f'{dest}/'),
    capture_output=True, text=True,
)
if r.returncode != 0:
    test_fail(f"-aAX --backup --backup-dir push -> rc={r.returncode}\n{r.stderr}")

bf = bak / 'd1' / 'd2' / 'file'
if not bf.is_file() or bf.read_bytes() != b'OLD':
    test_fail(f"backup file missing or wrong content: {bf}")
for d in (bak / 'd1', bak / 'd1' / 'd2'):
    if not d.is_dir():
        test_fail(f"backup intermediate dir not created: {d}")

if (dest / 'd1' / 'd2' / 'file').read_bytes() != b'NEW':
    test_fail("dest/d1/d2/file not updated to NEW")

print("backup-acl-xattr-cache: make_backup_dir_tree cache_tmp_acl/xattr -> "
      "bak/d1, bak/d2 created with propagated ACL+xattr")

#!/usr/bin/env python3

import os
import platform
import shutil

from harness.rsync import (
    SCRATCHDIR, acl_get, acl_set, forced_protocol, rmtree, run_rsync,
    test_fail, test_skipped,
)

NEW_UID = 60002
STALE_UID = 60009

if platform.system() != 'Linux':
    test_skipped("POSIX ACL test is Linux-only (setfacl/getfacl semantics)",
                 capability='linux_acl')
if not shutil.which('setfacl') or not shutil.which('getfacl'):
    test_skipped("setfacl/getfacl not available", capability='linux_acl')
if '"ACLs": true' not in run_rsync('-VV', check=True, capture_output=True).stdout:
    test_skipped("rsync built without ACL support", capability='linux_acl')
proto = forced_protocol()
if proto is not None and proto < 30:
    test_skipped(f"ACL transfer requires protocol 30+ (negotiated {proto})",
                 capability='protocol_30')

base = SCRATCHDIR / 'acls-unpinnable'
src = base / 'src'
dest = base / 'dest'
rmtree(base)
(src / 'dropbox').mkdir(parents=True)
(src / 'top.txt').write_text('top\n')
(dest / 'dropbox').mkdir(parents=True)

if not acl_set(f'user:{NEW_UID}:rwx', src / 'dropbox'):
    test_skipped('filesystem has ACLs disabled', capability='linux_acl')
os.chmod(src / 'dropbox', 0o300)
acl_set(f'user:{STALE_UID}:rwx', dest / 'dropbox')
os.chmod(dest / 'dropbox', 0o300)

proc = run_rsync('-aA', f'{src}/', f'{dest}/', check=False, capture_output=True)
if proc.returncode not in (0, 23):
    test_fail(f"rsync exited {proc.returncode}:\n{proc.stderr}")

acl = acl_get(dest / 'dropbox')
if f'user:{NEW_UID}:' not in acl:
    test_fail("--acls did not apply the source ACL to the un-pinnable (0300) "
              f"destination directory (stale/missing ACL); getfacl:\n{acl}")
if f'user:{STALE_UID}:' in acl:
    test_fail("--acls left the stale destination ACL entry in place on the "
              f"un-pinnable (0300) directory -- a revocation did not propagate; "
              f"getfacl:\n{acl}")

print("acls-unpinnable: --acls updates the ACL of a no-owner-read (0300) dir")

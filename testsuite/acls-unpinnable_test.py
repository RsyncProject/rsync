#!/usr/bin/env python3
# --acls must update a destination ACL even when the destination entry cannot be
# pinned with O_RDONLY|O_NOFOLLOW -- e.g. a pre-existing no-owner-read (0300)
# directory, whose held_fd open fails for a non-root receiver.
#
# The receiver's hardened ACL path applies via a held fd, or via the *xattrat
# syscalls (dirfd+leaf, AT_SYMLINK_NOFOLLOW) on Linux 6.13+.  Where neither is
# available -- no held fd (un-pinnable entry) AND no *xattrat (pre-6.13 / BSD) --
# rsync falls back to the path-based sys_acl_set_file() so --acls stays
# functional (project policy: prefer functionality where the OS cannot offer the
# race-safe primitive; the parent-symlink race is an accepted residual there,
# and a current kernel takes the secure xattrat path).
#
# An earlier revision skipped that fallback with a warning + success, silently
# leaving the destination ACL STALE -- so a revoked/changed source ACL did not
# propagate while rsync reported success.  This asserts the new ACL lands and the
# stale entry is gone.  It exercises the path-based fallback when run non-root on
# a no-*xattrat kernel; elsewhere it goes through the held-fd / xattrat path and
# still asserts the same functional outcome.

import os
import platform
import shutil
import subprocess

from rsyncfns import (
    SCRATCHDIR, forced_protocol, rmtree, run_rsync, test_fail, test_skipped,
)

NEW_UID = 60002          # the source ACL grant that must propagate
STALE_UID = 60009        # a different grant pre-seeded on the dest; must be gone

if platform.system() != 'Linux':
    test_skipped("POSIX ACL test is Linux-only (setfacl/getfacl semantics)")
if not shutil.which('setfacl') or not shutil.which('getfacl'):
    test_skipped("setfacl/getfacl not available")
if '"ACLs": true' not in run_rsync('-VV', check=True, capture_output=True).stdout:
    test_skipped("rsync built without ACL support")
proto = forced_protocol()
if proto is not None and proto < 30:
    test_skipped(f"ACL transfer requires protocol 30+ (negotiated {proto})")


def setfacl(spec, path):
    if subprocess.run(['setfacl', '-m', spec, str(path)]).returncode != 0:
        test_skipped("filesystem has ACLs disabled (setfacl failed)")


def getfacl(path) -> str:
    # POSIXLY_CORRECT in the suite: plain getfacl only (no -c/-E).
    return subprocess.run(['getfacl', str(path)],
                          capture_output=True, text=True).stdout


base = SCRATCHDIR / 'acls-unpinnable'
src = base / 'src'
dest = base / 'dest'
rmtree(base)
(src / 'dropbox').mkdir(parents=True)
(src / 'top.txt').write_text('top\n')
(dest / 'dropbox').mkdir(parents=True)

# Source: the grant that must propagate.  Dest: a pre-existing 0300 directory
# carrying a *different* (stale) grant, so the sync must change its ACL -- and
# the dir is un-pinnable (no owner read) for a non-root receiver at apply time.
setfacl(f'user:{NEW_UID}:rwx', src / 'dropbox')
os.chmod(src / 'dropbox', 0o300)
setfacl(f'user:{STALE_UID}:rwx', dest / 'dropbox')
os.chmod(dest / 'dropbox', 0o300)

# The 0300 dirs are unreadable, so listing them errors (exit 23); the directory
# entry + its ACL still transfer, which is what we assert.  Accept 0 or 23.
proc = run_rsync('-aA', f'{src}/', f'{dest}/', check=False, capture_output=True)
if proc.returncode not in (0, 23):
    test_fail(f"rsync exited {proc.returncode}:\n{proc.stderr}")

acl = getfacl(dest / 'dropbox')
if f'user:{NEW_UID}:' not in acl:
    test_fail("--acls did not apply the source ACL to the un-pinnable (0300) "
              f"destination directory (stale/missing ACL); getfacl:\n{acl}")
if f'user:{STALE_UID}:' in acl:
    test_fail("--acls left the stale destination ACL entry in place on the "
              f"un-pinnable (0300) directory -- a revocation did not propagate; "
              f"getfacl:\n{acl}")

print("acls-unpinnable: --acls updates the ACL of a no-owner-read (0300) dir")

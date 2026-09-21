#!/usr/bin/env python3
"""Coverage: --fake-super -A stores/loads ACLs via the user.rsync.%aacl /
%dacl xattrs (xattrs.c get_xattr_acl/set_xattr_acl/del_def_xattr_acl and the
acls.c am_root<0 IVAL/SIVAL pack/unpack paths).

Three legs, all with -A --fake-super on the *receiving* side:

  1. real-ACL src  -> fake-super dst    (acls.c set_xattr_acl path: SIVAL pack
                                         + xattrs.c set_xattr_acl)
  2. fake-super dst -> fake-super dst2  (acls.c get_xattr_acl path: IVAL
                                         unpack on the SENDER side via -M
                                         --fake-super, then set_xattr_acl
                                         again on the receiver)
  3. drop the source dir's default ACL and re-sync dst -> del_def_xattr_acl
     removes the stale %dacl xattr.

Structural assertions only (xattr presence/absence): the per-field ACL
round-trip is acls_test.py's job; this test exists to cover the xattr-
encoding branch that the regular -A tests (am_root>=0) never reach.
"""

import os
import platform
import subprocess

from rsyncfns import (
    SCRATCHDIR, FROMDIR,
    acls_supported, makepath, rmtree, rsync_argv,
    test_fail, test_skipped, xattrs_supported,
)

# The fake-super ACL encoding stores ACLs under the Linux-only `user.rsync.`
# xattr namespace (xattrs.c USERNAME_PREFIX under HAVE_LINUX_XATTRS); elsewhere
# the prefix is `rsync.` and the access path uses setfacl/os.getxattr that don't
# exist or differ (macOS, the BSDs, Cygwin).  This test targets that Linux path.
if platform.system() != 'Linux':
    test_skipped("fake-super ACL xattrs use the Linux user.rsync.* namespace")

if not acls_supported():
    test_skipped("ACLs not supported on this filesystem")
if not xattrs_supported():
    test_skipped("xattrs not supported on this filesystem")

src = FROMDIR
dst = SCRATCHDIR / 'fakesuper-dst'
dst2 = SCRATCHDIR / 'fakesuper-dst2'
for d in (src, dst, dst2):
    rmtree(d)
makepath(src)

# Source: a file with a named-user access ACL entry, and a directory with
# a default ACL (so both %aacl and %dacl encodings are exercised, and the
# id_access cnt>0 branch in acls.c pack/unpack fires).
(src / 'f').write_text('hello\n')
sd = src / 'd'
sd.mkdir()
(sd / 'g').write_text('world\n')

r = subprocess.run(['setfacl', '-m', f'u:{os.getuid()}:rwx', str(src / 'f')],
                   capture_output=True, text=True)
if r.returncode != 0:
    test_skipped(f"setfacl on file failed: {r.stderr!r}")
r = subprocess.run(['setfacl', '-d', '-m', f'u:{os.getuid()}:rwx', str(sd)],
                   capture_output=True, text=True)
if r.returncode != 0:
    test_skipped(f"setfacl -d on dir failed: {r.stderr!r}")


def has_xattr(path, name):
    try:
        os.getxattr(str(path), name, follow_symlinks=False)
        return True
    except OSError:
        return False


def sync(args, frm, to):
    r = subprocess.run(rsync_argv(*args, f'{frm}/', f'{to}/'),
                       capture_output=True, text=True)
    if r.returncode != 0:
        test_fail(f"rsync {' '.join(args)} {frm} -> {to} "
                  f"(rc={r.returncode}):\n{r.stderr}")
    return r


# --- leg 1: real-ACL src -> fake-super dst (set_xattr_acl + SIVAL pack) ----
# In a local copy the client is the SENDER and the forked "server" is the
# RECEIVER, so --fake-super on the receiving side is `-M--fake-super`.
sync(['-rA', '-M--fake-super'], src, dst)
if not has_xattr(dst / 'f', 'user.rsync.%aacl'):
    test_fail("--fake-super -A should have stored user.rsync.%aacl on dst/f")
if not has_xattr(dst / 'd', 'user.rsync.%dacl'):
    test_fail("--fake-super -A should have stored user.rsync.%dacl on dst/d")

# --- leg 2: fake-super dst -> fake-super dst2 (get_xattr_acl + IVAL unpack)
# Both sides in fake-super mode: sender (client) READS ACLs from the
# %aacl/%dacl xattrs (get_xattr_acl + IVAL unpack), receiver (-M side)
# WRITES them back (set_xattr_acl).
sync(['-rA', '--fake-super', '-M--fake-super'], dst, dst2)
if not has_xattr(dst2 / 'f', 'user.rsync.%aacl'):
    test_fail("fake-super -> fake-super -A round-trip lost %aacl on dst2/f")
if not has_xattr(dst2 / 'd', 'user.rsync.%dacl'):
    test_fail("fake-super -> fake-super -A round-trip lost %dacl on dst2/d")

# --- leg 3: drop the source dir's default ACL, re-sync -> del_def_xattr_acl
subprocess.run(['setfacl', '-k', str(sd)], check=True)
sync(['-rA', '-M--fake-super'], src, dst)
if has_xattr(dst / 'd', 'user.rsync.%dacl'):
    test_fail("re-sync after `setfacl -k` should have removed %dacl from dst/d "
              "(del_def_xattr_acl)")

print("fake-super-acl-xattr: %aacl/%dacl set+get round-trip + del_def ok")

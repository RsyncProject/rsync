#!/usr/bin/env python3

import os
import platform
import subprocess

from harness.rsync import (
    SCRATCHDIR, FROMDIR,
    acls_supported, makepath, rmtree, rsync_argv,
    test_fail, test_skipped, xattrs_supported,
)

if platform.system() != 'Linux':
    test_skipped("fake-super ACL xattrs use the Linux user.rsync.* namespace",
                 capability='linux_xattr')

if not acls_supported():
    test_skipped("ACLs not supported on this filesystem", capability='acl')
if not xattrs_supported():
    test_skipped("xattrs not supported on this filesystem", capability='xattr')

src = FROMDIR
dst = SCRATCHDIR / 'fakesuper-dst'
dst2 = SCRATCHDIR / 'fakesuper-dst2'
for d in (src, dst, dst2):
    rmtree(d)
makepath(src)

(src / 'f').write_text('hello\n')
sd = src / 'd'
sd.mkdir()
(sd / 'g').write_text('world\n')

r = subprocess.run(['setfacl', '-m', f'u:{os.getuid()}:rwx', str(src / 'f')],
                   capture_output=True, text=True)
if r.returncode != 0:
    test_skipped(f"setfacl on file failed: {r.stderr!r}", capability='acl')
r = subprocess.run(['setfacl', '-d', '-m', f'u:{os.getuid()}:rwx', str(sd)],
                   capture_output=True, text=True)
if r.returncode != 0:
    test_skipped(f"setfacl -d on dir failed: {r.stderr!r}", capability='acl')

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

sync(['-rA', '-M--fake-super'], src, dst)
if not has_xattr(dst / 'f', 'user.rsync.%aacl'):
    test_fail("--fake-super -A should have stored user.rsync.%aacl on dst/f")
if not has_xattr(dst / 'd', 'user.rsync.%dacl'):
    test_fail("--fake-super -A should have stored user.rsync.%dacl on dst/d")

sync(['-rA', '--fake-super', '-M--fake-super'], dst, dst2)
if not has_xattr(dst2 / 'f', 'user.rsync.%aacl'):
    test_fail("fake-super -> fake-super -A round-trip lost %aacl on dst2/f")
if not has_xattr(dst2 / 'd', 'user.rsync.%dacl'):
    test_fail("fake-super -> fake-super -A round-trip lost %dacl on dst2/d")

subprocess.run(['setfacl', '-k', str(sd)], check=True)
sync(['-rA', '-M--fake-super'], src, dst)
if has_xattr(dst / 'd', 'user.rsync.%dacl'):
    test_fail("re-sync after `setfacl -k` should have removed %dacl from dst/d "
              "(del_def_xattr_acl)")

print("fake-super-acl-xattr: %aacl/%dacl set+get round-trip + del_def ok")

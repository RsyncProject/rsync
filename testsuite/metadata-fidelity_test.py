#!/usr/bin/env python3
"""Workstream-1 invariant group B -- metadata fidelity.

Driven by generator.c's *_differ predicates (reference.md Part 2): under the
matching -a sub-options, rsync makes the destination's metadata match the
source's, and unchanged_attrs() treats a single attribute difference as a
reason to re-stamp. We assert each attribute is reproduced -- PARTITIONED so
that legitimately-privilege/feature-dependent attributes (owner, group, ACL
ids, xattr namespace, devices) are only asserted when the environment can
actually preserve them, and SKIP cleanly otherwise.

Covered: perms, exec-bit (-E), times incl. nanoseconds (mtime_differs ~395),
owner/group (ownership_differs ~428, privilege-partitioned), hardlinks (-H),
devices (root-only), ACLs (-A, feature+ability gated), xattrs (-X, feature
gated), omit-dir-times (-O) and omit-link-times (--omit-link-times).
"""

import os
import platform
import shutil
import stat
import subprocess

from rsyncfns import (
    FROMDIR, SCRATCHDIR, TODIR,
    assert_hardlinked, assert_mode, assert_mtime_close,
    makepath, rmtree, run_rsync, test_fail,
    xattr_dump, xattr_set, xattrs_supported,
)
from equiv_fns import am_root


VV = run_rsync('-VV', check=True, capture_output=True).stdout


def _fresh(*dirs):
    for d in (FROMDIR, TODIR, *dirs):
        rmtree(d)
    makepath(FROMDIR)


# --------------------------------------------------------------------------
# B-perms -- -p reproduces the full permission bits.
# --------------------------------------------------------------------------
_fresh()
for nm, mode in (('a', 0o644), ('b', 0o600), ('c', 0o751), ('d', 0o444)):
    (FROMDIR / nm).write_text(nm)
    os.chmod(FROMDIR / nm, mode)
run_rsync('-a', f'{FROMDIR}/', f'{TODIR}/')
for nm, mode in (('a', 0o644), ('b', 0o600), ('c', 0o751), ('d', 0o444)):
    assert_mode(TODIR / nm, mode, label=f'B-perms {nm}')


# --------------------------------------------------------------------------
# B-exec -- -E (--executability) propagates ONLY the executable bit.
# --------------------------------------------------------------------------
# Without -p, a non-exec source file's exec bit must be cleared on a dest that
# has it; an exec source file's exec bit must be set. Non-exec permission bits
# are NOT forced to match (that's -p's job), so we assert exactly the exec bit.
_fresh()
(FROMDIR / 'prog').write_text('#!/bin/sh\n')
(FROMDIR / 'data').write_text('plain\n')
os.chmod(FROMDIR / 'prog', 0o700)   # has exec
os.chmod(FROMDIR / 'data', 0o600)   # no exec
# Pre-seed dest with the OPPOSITE exec state so -E has to flip both.
makepath(TODIR)
(TODIR / 'prog').write_text('old\n')
(TODIR / 'data').write_text('old\n')
os.chmod(TODIR / 'prog', 0o600)     # missing exec, must gain it
os.chmod(TODIR / 'data', 0o700)     # has exec, must lose it
run_rsync('-rtE', f'{FROMDIR}/', f'{TODIR}/')
if not (os.stat(TODIR / 'prog').st_mode & 0o111):
    test_fail('B-exec: -E did not set the exec bit on an executable source')
if os.stat(TODIR / 'data').st_mode & 0o111:
    test_fail('B-exec: -E did not clear the exec bit on a non-executable source')


# --------------------------------------------------------------------------
# B-times -- -t reproduces mtime; nanoseconds preserved where representable.
# --------------------------------------------------------------------------
_fresh()
(FROMDIR / 'f').write_text('timed\n')
# A whole-second base plus a non-zero nanosecond remainder.
WHOLE = 1_500_000_000
NSEC = 123_456_789
os.utime(FROMDIR / 'f', ns=(WHOLE * 1_000_000_000 + NSEC,
                            WHOLE * 1_000_000_000 + NSEC))
src_ns = os.stat(FROMDIR / 'f').st_mtime_ns

run_rsync('-a', f'{FROMDIR}/', f'{TODIR}/')
# Whole-second mtime must always match.
assert_mtime_close(TODIR / 'f', WHOLE, tol=1.0, label='B-times whole-second')

# Nanosecond sub-assertion: only meaningful if BOTH the source filesystem AND
# rsync actually represent sub-second mtimes. Probe the source: if the fs
# truncated our nsec to 0, we cannot assert nsec fidelity -- degrade cleanly.
dst_ns = os.stat(TODIR / 'f').st_mtime_ns
src_sub = src_ns % 1_000_000_000
if src_sub == 0 or '"symtimes": true' not in VV:
    print('B-times: nanosecond sub-assertion skipped (fs/rsync lacks '
          'sub-second mtime representation)')
else:
    if dst_ns != src_ns:
        test_fail(f'B-times nsec: dst mtime_ns {dst_ns} != src {src_ns} '
                  '(sub-second mtime not preserved)')


# --------------------------------------------------------------------------
# B-owner-group -- -og.  PARTITIONED by privilege.
# --------------------------------------------------------------------------
# ownership_differs(): UID is only reproduced when am_root && uid_ndx; GID can
# be set by an unprivileged caller ONLY for groups it belongs to. We therefore:
#   * always assert the dest GID matches the source for a file we chgrp to one
#     of OUR OWN supplementary groups (no privilege needed);
#   * assert UID equality ONLY when running as root.
_fresh()
(FROMDIR / 'f').write_text('owned\n')

# Group test: pick a secondary group we already belong to (so chgrp succeeds
# unprivileged). Skip the group sub-check if we have no usable second group.
my_gid = os.getgid()
groups = [g for g in os.getgroups() if g != my_gid]
if groups:
    target_gid = groups[0]
    os.chown(FROMDIR / 'f', -1, target_gid)
    run_rsync('-a', f'{FROMDIR}/', f'{TODIR}/')
    dst_gid = os.stat(TODIR / 'f').st_gid
    if dst_gid != target_gid:
        test_fail(f'B-group: -g did not reproduce gid {target_gid} '
                  f'(dst gid {dst_gid})')
else:
    print('B-group: skipped (no usable secondary group to chgrp into)')

# Owner test: only assert under root; chown(2) to an arbitrary uid needs root.
if am_root():
    rmtree(TODIR)
    os.chown(FROMDIR / 'f', 5000, -1)
    run_rsync('-a', f'{FROMDIR}/', f'{TODIR}/')
    dst_uid = os.stat(TODIR / 'f').st_uid
    if dst_uid != 5000:
        test_fail(f'B-owner: -o did not reproduce uid 5000 (dst {dst_uid})')
else:
    print('B-owner: uid-equality sub-check skipped (needs root to set/verify '
          'arbitrary ownership)')


# --------------------------------------------------------------------------
# B-hardlinks -- -H preserves a hard-link group (shared inode on the dest).
# --------------------------------------------------------------------------
_fresh()
(FROMDIR / 'h1').write_text('linked\n')
os.link(FROMDIR / 'h1', FROMDIR / 'h2')
os.link(FROMDIR / 'h1', FROMDIR / 'h3')
# Sanity: source really is one inode.
assert_hardlinked(FROMDIR / 'h1', FROMDIR / 'h2', label='B-hardlinks src')
run_rsync('-aH', f'{FROMDIR}/', f'{TODIR}/')
assert_hardlinked(TODIR / 'h1', TODIR / 'h2', label='B-hardlinks h1==h2')
assert_hardlinked(TODIR / 'h1', TODIR / 'h3', label='B-hardlinks h1==h3')
# Without -H the destination files must NOT be linked (proves -H is load-
# bearing, not an accident of the filesystem).
rmtree(TODIR)
run_rsync('-a', f'{FROMDIR}/', f'{TODIR}/')
s2, s3 = os.stat(TODIR / 'h1'), os.stat(TODIR / 'h2')
if (s2.st_dev, s2.st_ino) == (s3.st_dev, s3.st_ino):
    test_fail('B-hardlinks: dest files share an inode WITHOUT -H '
              '(hard-link preservation is not actually being controlled by -H)')


# --------------------------------------------------------------------------
# B-devices -- -D reproduces a device node's type+rdev.  ROOT-ONLY.
# --------------------------------------------------------------------------
# mknod(2) of a char/block device needs root; mirror devices-fake's pattern of
# skipping cleanly when not privileged.
if not am_root():
    print('B-devices: skipped (mknod needs root)')
else:
    _fresh()
    dev = FROMDIR / 'nulldev'
    try:
        os.mknod(dev, 0o600 | 0o020000, os.makedev(1, 3))  # S_IFCHR
    except (PermissionError, OSError) as e:
        print(f'B-devices: skipped (mknod failed: {e})')
    else:
        run_rsync('-aD', f'{FROMDIR}/', f'{TODIR}/')
        dst = TODIR / 'nulldev'
        st = os.stat(dst, follow_symlinks=False)
        if not (platform.system() and os.path.exists(dst)):
            test_fail('B-devices: device node not created on dest')
        if not stat.S_ISCHR(st.st_mode):
            test_fail('B-devices: dest is not a character device')
        if st.st_rdev != os.makedev(1, 3):
            test_fail(f'B-devices: dest rdev {st.st_rdev} != source '
                      f'{os.makedev(1, 3)}')


# --------------------------------------------------------------------------
# B-acls -- -A preserves a POSIX ACL.  FEATURE + ABILITY gated.
# --------------------------------------------------------------------------
if '"ACLs": true' not in VV:
    print('B-acls: skipped (rsync built without ACL support)')
elif platform.system() != 'Linux' or not (shutil.which('setfacl')
                                           and shutil.which('getfacl')):
    print('B-acls: skipped (no setfacl/getfacl on this platform)')
else:
    _fresh()
    (FROMDIR / 'f').write_text('acl\n')
    # Grant an extra ACL entry to a group we belong to (no privilege needed),
    # falling back to a skip if the filesystem rejects ACLs.
    gid = os.getgid()
    r = subprocess.run(['setfacl', '-m', f'g:{gid}:rwx', str(FROMDIR / 'f')])
    if r.returncode != 0:
        print('B-acls: skipped (filesystem rejected setfacl)')
    else:
        run_rsync('-aA', f'{FROMDIR}/', f'{TODIR}/')

        def acl_of(p):
            out = subprocess.run(['getfacl', '-cE', str(p)],
                                 capture_output=True, text=True).stdout
            return '\n'.join(sorted(l for l in out.splitlines() if l.strip()))
        if acl_of(FROMDIR / 'f') != acl_of(TODIR / 'f'):
            test_fail('B-acls: -A did not reproduce the source ACL\n'
                      f'src:\n{acl_of(FROMDIR / "f")}\n'
                      f'dst:\n{acl_of(TODIR / "f")}')


# --------------------------------------------------------------------------
# B-xattrs -- -X preserves a user-namespace xattr.  FEATURE gated.
# --------------------------------------------------------------------------
if not xattrs_supported():
    print('B-xattrs: skipped (no usable xattr surface)')
else:
    _fresh()
    (FROMDIR / 'f').write_text('xattr\n')
    try:
        xattr_set('test.attr', 'hello-world', FROMDIR / 'f')
    except (PermissionError, OSError) as e:
        print(f'B-xattrs: skipped (filesystem rejected xattr set: {e})')
    else:
        run_rsync('-aX', f'{FROMDIR}/', f'{TODIR}/')

        # xattr_dump prefixes each file with a "# file: <path>" header; strip
        # the per-file headers (and any blank lines) so we compare only the
        # name="value" payload, which is what -X must reproduce.
        def _xpayload(p):
            return '\n'.join(
                ln for ln in xattr_dump(p).splitlines()
                if ln.strip() and not ln.startswith('# file:'))
        src_x = _xpayload(FROMDIR / 'f')
        dst_x = _xpayload(TODIR / 'f')
        if not src_x:
            test_fail('B-xattrs: source xattr not set as expected')
        if src_x != dst_x:
            test_fail(f'B-xattrs: -X did not reproduce xattrs\n'
                      f'src: {src_x!r}\ndst: {dst_x!r}')


# --------------------------------------------------------------------------
# B-omit-dir-times -- -O preserves FILE mtimes but leaves DIR mtimes alone.
# --------------------------------------------------------------------------
OLD = 1_400_000_000
_fresh()
makepath(FROMDIR / 'sub')
(FROMDIR / 'sub' / 'f').write_text('x\n')
for p in (FROMDIR / 'sub' / 'f', FROMDIR / 'sub', FROMDIR):
    os.utime(p, (OLD, OLD))
run_rsync('-rlt', '-O', f'{FROMDIR}/', f'{TODIR}/')
assert_mtime_close(TODIR / 'sub' / 'f', OLD, tol=1.0, label='B-O file mtime')
if abs(os.stat(TODIR / 'sub').st_mtime - OLD) <= 1:
    test_fail('B-omit-dir-times: -O preserved a directory mtime instead of '
              'omitting it')


# --------------------------------------------------------------------------
# B-omit-link-times -- --omit-link-times preserves a symlink but omits its
# mtime (where the platform records symlink mtimes).
# --------------------------------------------------------------------------
_fresh()
(FROMDIR / 'target').write_text('t\n')
os.symlink('target', FROMDIR / 'sl')
try:
    os.utime(FROMDIR / 'sl', (OLD, OLD), follow_symlinks=False)
except (NotImplementedError, OSError):
    print('B-omit-link-times: skipped (no symlink-mtime support here)')
else:
    if '"symtimes": true' not in VV:
        print('B-omit-link-times: skipped (rsync built without symtimes)')
    else:
        run_rsync('-rlt', '--omit-link-times', f'{FROMDIR}/', f'{TODIR}/')
        dst = TODIR / 'sl'
        if not os.path.islink(dst):
            test_fail('B-omit-link-times: symlink not copied')
        if abs(os.lstat(dst).st_mtime - OLD) <= 1:
            test_fail('B-omit-link-times: --omit-link-times did not omit the '
                      'symlink mtime')

print('metadata-fidelity: perms/exec/times+nsec/owner-group/hardlinks/'
      'devices/acls/xattrs/omit-times verified (partitioned by privilege+feature)')

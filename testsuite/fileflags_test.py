#!/usr/bin/env python3
# Python rewrite of testsuite/fileflags.test.
#
# Test rsync preserving file flags via --fileflags. rsync's --fileflags
# rides on top of:
#   - BSD/macOS:  chflags(2) / fchflags(2)
#   - Linux:      ioctl(FS_IOC_GETFLAGS / FS_IOC_SETFLAGS), aka chattr(1)
#
# This exercises both, using whichever userland tool is available (chflags
# on BSD/macOS, chattr on Linux). It self-skips on systems where rsync was
# built without fileflags support, where neither tool is installed, or
# where the scratch filesystem doesn't support the flags we need.

import atexit
import filecmp
import os
import re
import shutil
import subprocess

import rsyncfns
from rsyncfns import (
    FROMDIR, TODIR, OUTFILE, RSYNC,
    run_rsync, rmtree, test_fail, test_skipped,
)


# Skip if rsync wasn't built with fileflags support.
if '"file_flags": true' not in run_rsync('-VV', capture_output=True).stdout:
    test_skipped("Rsync is configured without fileflags support")

# --fileflags requires the varint flag encoding negotiated by
# CF_VARINT_FLIST_FLAGS, which only happens at protocol >= 30. Under
# check29 the $RSYNC string includes --protocol=29 and compat.c aborts with
# "Both rsync versions must be at least 3.2.0 for --fileflags" -- skip
# rather than fail.
m = re.search(r'--protocol[ =](\d+)', RSYNC)
if m and int(m.group(1)) < 30:
    test_skipped("--fileflags requires protocol >= 30")


# --- platform-appropriate userland tool + readback ------------------------

def _ok(*argv) -> bool:
    return subprocess.run(argv, capture_output=True).returncode == 0


if shutil.which('chflags'):
    # BSD/macOS path.
    def set_nodump(p):  return _ok('chflags', 'nodump', str(p))
    def set_uchg(p):    return _ok('chflags', 'uchg', str(p))
    def clear_flags(*ps): _ok('chflags', '0', *(str(p) for p in ps))

    if _ok('stat', '-f', '%f', '.'):
        def show_flags(p):
            return subprocess.run(['stat', '-f', '%f', str(p)],
                                  capture_output=True, text=True).stdout.strip()
    elif _ok('ls', '-lod', '.'):
        def show_flags(p):
            out = subprocess.run(['ls', '-lod', str(p)],
                                 capture_output=True, text=True).stdout
            return out.split()[4] if out else '-'
    else:
        test_skipped("No way to read st_flags on this host (no stat -f or ls -lo)")
elif shutil.which('chattr') and shutil.which('lsattr'):
    # Linux path -- chattr 'd' is owner-settable on filesystems that support
    # it (ext2/3/4, xfs, btrfs, f2fs, ...). 'i' (immutable) requires
    # CAP_LINUX_IMMUTABLE so set_uchg fails for ordinary users and the uchg
    # portions self-skip.
    def set_nodump(p):  return _ok('chattr', '+d', str(p))
    def set_uchg(p):    return _ok('chattr', '+i', str(p))
    def clear_flags(*ps): _ok('chattr', '=', *(str(p) for p in ps))

    def show_flags(p):
        # lsattr's first column is the 20-char attribute string, but most of
        # those bits are fs-internal (extent format, htree, inline data, ...)
        # and aren't part of what rsync transfers. Strip down to just the
        # transferable letters (a, d, i, u) so we don't fail on e.g. a small
        # dest inode that didn't get the 'e' bit when ext4 inlined its data.
        out = subprocess.run(['lsattr', '-d', str(p)],
                             capture_output=True, text=True).stdout
        if not out:
            return '-'
        s = out.split()[0]
        keep = ''.join(c for c in s if c in 'adiu')
        return keep or '-'
else:
    test_skipped("No chflags(1) or chattr(1) command on this host")


# Best-effort: strip flags off everything under from/to at exit so the
# runner can rm the scratch tree even if an immutable file is left behind
# (only reachable as root / on BSD, where set_uchg succeeds).
def _unlock_all():
    for base in (FROMDIR, TODIR):
        if not base.exists():
            continue
        for root, dirs, files in os.walk(base):
            for name in files + dirs:
                clear_flags(os.path.join(root, name))
        clear_flags(base)


atexit.register(_unlock_all)


def fail(msg):
    _unlock_all()
    test_fail(msg)


# --- basic flag round-trip ------------------------------------------------

rmtree(FROMDIR)
rmtree(TODIR)
FROMDIR.mkdir(parents=True)
for name in ('plain', 'nodump', 'uchg'):
    (FROMDIR / name).write_text("hi\n")
(FROMDIR / 'dir').mkdir()
(FROMDIR / 'dir' / 'inner').write_text("hi\n")

# Set the user-settable "nodump" flag on one file.
if not set_nodump(FROMDIR / 'nodump'):
    test_skipped("Filesystem does not support the nodump flag")

# Try the user-immutable flag too; on Linux this needs CAP_LINUX_IMMUTABLE
# (root), and some BSD filesystems disallow it -- if so, drop the uchg part.
have_uchg = set_uchg(FROMDIR / 'uchg')
if not have_uchg:
    (FROMDIR / 'uchg').unlink()

# Also set nodump on the directory to check that dir flags travel.
set_nodump(FROMDIR / 'dir')

src_nodump = show_flags(FROMDIR / 'nodump')
src_plain = show_flags(FROMDIR / 'plain')
src_dir = show_flags(FROMDIR / 'dir')
print(f"source flags: plain={src_plain} nodump={src_nodump} dir={src_dir}")

rsyncfns.TLS_ARGS = '--fileflags'

print(f'Running: rsync -rtgvvv --fileflags "{FROMDIR}/" "{TODIR}/"')
run_rsync('-rtgvvv', '--fileflags', f'{FROMDIR}/', f'{TODIR}/')

for f in ('plain', 'nodump', 'dir/inner', 'dir'):
    s = show_flags(FROMDIR / f)
    d = show_flags(TODIR / f)
    if s != d:
        fail(f"flags mismatch on {f}: source={s} dest={d}")
    print(f"ok: {f} flags={d}")

if have_uchg:
    s = show_flags(FROMDIR / 'uchg')
    d = show_flags(TODIR / 'uchg')
    if s != d:
        fail(f"flags mismatch on uchg: source={s} dest={d}")
    print(f"ok: uchg flags={d}")
    clear_flags(FROMDIR / 'uchg', TODIR / 'uchg')

# Confirm the itemized output reports an 'f' change on a second run after we
# clear the flag on the destination.
clear_flags(TODIR / 'nodump')
if show_flags(TODIR / 'nodump') == src_nodump:
    fail("could not clear flags on dest nodump")

proc = run_rsync('-rtgi', '--fileflags', f'{FROMDIR}/', f'{TODIR}/',
                 capture_output=True)
print(proc.stdout)
if not re.search(r'(?m)^\.f\.+f.* nodump$', proc.stdout):
    fail("expected itemized 'f' (flags) change on nodump in second run")


# --- SECURITY: --unsafe-fileflags option ----------------------------------
# Smoke test: confirm the option parses and that --fileflags
# --unsafe-fileflags round-trips a SAFE_FILEFLAGS bit cleanly.

rmtree(FROMDIR)
rmtree(TODIR)
FROMDIR.mkdir()
TODIR.mkdir()
(FROMDIR / 'plain').write_text("hi\n")
set_nodump(FROMDIR / 'plain')

run_rsync('-rt', '--fileflags', '--unsafe-fileflags', f'{FROMDIR}/', f'{TODIR}/')

if show_flags(FROMDIR / 'plain') != show_flags(TODIR / 'plain'):
    fail("unsafe-fileflags should still propagate flags within SAFE_FILEFLAGS")


# --- DEFERRED IMMUTABLE BITS ON DIRECTORIES -------------------------------
# If the source has a uchg (immutable) directory with children, --fileflags
# must populate the children BEFORE the immutable bit is set on the dest dir
# -- otherwise the dest dir's +i blocks creating children inside it. rsync
# holds back immutable bits during recv_generator (ATTRS_DELAY_IMMUTABLE)
# and re-applies them in touch_up_dirs after all children land. Only run
# this if the test user can set uchg.

if have_uchg:
    rmtree(FROMDIR)
    rmtree(TODIR)
    FROMDIR.mkdir()
    TODIR.mkdir()
    (FROMDIR / 'locked_dir').mkdir()
    (FROMDIR / 'locked_dir' / 'inner1').write_text("inner1\n")
    (FROMDIR / 'locked_dir' / 'inner2').write_text("inner2\n")
    if not set_uchg(FROMDIR / 'locked_dir'):
        rmtree(FROMDIR / 'locked_dir')
        have_uchg = False

if have_uchg:
    src_locked = show_flags(FROMDIR / 'locked_dir')
    print(f"source locked_dir flags: {src_locked} (should contain uchg)")

    # The transfer must succeed even though source dir is uchg -- rsync must
    # defer the uchg apply until after children populate.
    proc = run_rsync('-rt', '--fileflags', f'{FROMDIR}/', f'{TODIR}/',
                     check=False, capture_output=True)
    if proc.returncode != 0:
        print(proc.stdout)
        print(proc.stderr)
        fail("rsync --fileflags failed on uchg source dir with children")

    for f in ('inner1', 'inner2'):
        if not os.access(TODIR / 'locked_dir' / f, os.R_OK):
            fail(f"child {f} missing under uchg dest dir (defer didn't fire)")

    dst_locked = show_flags(TODIR / 'locked_dir')
    if src_locked != dst_locked:
        fail(f"dest locked_dir flags={dst_locked} != source {src_locked} "
             "(touch_up_dirs didn't re-apply)")
    print(f"ok: deferred-immutable on dir: src={src_locked} dst={dst_locked}, "
          "children present")

    clear_flags(FROMDIR / 'locked_dir', TODIR / 'locked_dir')


# --- --force-change restores flags after the transfer ---------------------
# The manpage says "--force-change ... The original flags are restored after
# the update." Verify the restore happens: dest has uchg, source has
# different content + no flags; after the transfer the dest content matches
# the source AND the dest still has uchg. Only runs if the user can set uchg.

if have_uchg:
    rmtree(FROMDIR)
    rmtree(TODIR)
    FROMDIR.mkdir()
    TODIR.mkdir()
    (FROMDIR / 'forced').write_text(
        "new content -- forced through uchg via --force-change\n")
    (TODIR / 'forced').write_text("old\n")
    if not set_uchg(TODIR / 'forced'):
        # Filesystem won't allow uchg here even though it worked on fromdir
        # earlier -- skip this block rather than fail the whole test.
        (TODIR / 'forced').unlink()
    else:
        dst_pre = show_flags(TODIR / 'forced')

        # --force-change without --fileflags: rsync should bully through the
        # uchg, replace the inode via temp+rename, and put uchg back.
        proc = run_rsync('--force-change', '-t',
                         str(FROMDIR / 'forced'), str(TODIR / 'forced'),
                         check=False, capture_output=True)
        if proc.returncode != 0:
            print(proc.stdout)
            print(proc.stderr)
            fail("rsync --force-change failed on uchg dest")

        dst_post = show_flags(TODIR / 'forced')
        if dst_pre != dst_post:
            fail(f"force_change did not restore flags: pre={dst_pre} post={dst_post}")

        if not filecmp.cmp(FROMDIR / 'forced', TODIR / 'forced', shallow=False):
            fail("force_change transfer left dest content stale")
        print(f"ok: --force-change restored flags={dst_post} after rename")

        clear_flags(TODIR / 'forced')

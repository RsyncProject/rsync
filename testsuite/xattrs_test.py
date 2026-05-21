#!/usr/bin/env python3
# Python rewrite of testsuite/xattrs.test (and, via a Makefile-built
# symlink, of xattrs-hlink.test).
#
# Test that rsync -X preserves extended attributes through a transfer,
# plus the --link-dest / --copy-dest / --fake-super interactions.
# The hlink variant additionally enables -H and tests that hard links
# survive alongside xattrs.

import os
import platform
import subprocess
import sys

from rsyncfns import (
    CHKDIR, FROMDIR, SCRATCHDIR, TMPDIR, TODIR, TOOLDIR,
    checkit, cp_touch, makepath, run_rsync, test_fail, test_skipped,
)


vv = run_rsync('-VV', check=True, capture_output=True)
if '"xattrs": true' not in vv.stdout:
    test_skipped("Rsync is configured without xattr support")

if platform.system() != 'Linux':
    test_skipped(f"xattr surface not implemented for {platform.system()}")

# Per-OS xattr surfaces -- Linux only here (other platforms test_skipped'd
# above). RSYNC_PREFIX is the name-prefix rsync itself looks for; RUSR is
# the prefix the test uses for "%stat"-style faux-attributes (must match
# how --fake-super stores them).
RSYNC_PREFIX = 'user.rsync'
RUSR = 'user.rsync'


def xset(name: str, value: str, *paths):
    """Set the named xattr to `value` on each of `paths`."""
    val = value.encode()
    for p in paths:
        try:
            os.setxattr(str(p), name.encode(), val)
        except OSError as e:
            raise OSError(f"setxattr {name}={value} on {p}: {e}")


def xls(*paths) -> str:
    """Mirror `getfattr -d` -- a per-path dump of name=value lines."""
    return subprocess.check_output(['getfattr', '-d', *(str(p) for p in paths)],
                                   text=True)


script_name = os.path.basename(sys.argv[0] if sys.argv[0] else __file__)
hlink_variant = 'hlink' in script_name

lnkdir = TMPDIR / 'lnk'
makepath(lnkdir, FROMDIR / 'foo' / 'bar')

(FROMDIR / 'file0').write_text("now\n")
(FROMDIR / 'file1').write_text("something\n")
(FROMDIR / 'file2').write_text("else\n")
(FROMDIR / 'foo' / 'file3').write_text("deep\n")
(FROMDIR / 'file4').write_text("normal\n")
(FROMDIR / 'foo' / 'bar' / 'file5').write_text("deeper\n")

makepath(CHKDIR / 'foo')
(CHKDIR / 'file1').write_text("wow\n")
cp_touch(FROMDIR / 'foo' / 'file3', CHKDIR / 'foo')

dirs = ['foo', 'foo/bar']
files = ['file0', 'file1', 'file2', 'foo/file3', 'file4', 'foo/bar/file5']

# Read the source dir's tls listing to extract its uid:gid -- used in
# the fake-super %stat encoding below. Format: "MODE SIZE UID.GID ..."
tls_out = subprocess.check_output(
    [str(TOOLDIR / 'tls'), str(FROMDIR / 'foo')], text=True,
).strip()
# Extract "UID.GID" -> "UID:GID"
import re
m = re.search(r' (\d+)\.(\d+) ', tls_out)
if not m:
    test_fail(f"can't parse uid/gid from tls output: {tls_out!r}")
uid_gid = f"{m.group(1)}:{m.group(2)}"

os.chdir(FROMDIR)

try:
    xset('user.foo', 'foo', 'file0')
except OSError:
    test_skipped("Unable to set an xattr")
xset('user.bar', 'bar', 'file0')

xset('user.short', 'this is short', 'file1')
xset('user.long',
     'this is a long attribute that will be truncated in the initial data send',
     'file1')
xset('user.good', 'this is good', 'file1')
xset('user.nice', 'this is nice', 'file1')

xset('user.foo', 'foo', 'file2')
xset('user.bar', 'bar', 'file2')
xset('user.long',
     'a long attribute for our new file that tests to ensure that this works',
     'file2')

xset('user.dir1', 'need to test directory xattrs too', 'foo')
xset('user.dir2', 'another xattr', 'foo')
xset('user.dir3', 'this is one last one for the moment', 'foo')

xset('user.dir4', 'another dir test', 'foo/bar')
xset('user.dir5', 'one last one', 'foo/bar')

xset('user.foo', 'new foo', 'foo/file3', 'foo/bar/file5')
xset('user.bar', 'new bar', 'foo/file3', 'foo/bar/file5')
xset('user.long',
     'this is also a long attribute that will be truncated in the initial data send',
     'foo/file3', 'foo/bar/file5')
xset(f'{RUSR}.equal',
     'this long attribute should remain the same and not need to be transferred',
     'foo/file3', 'foo/bar/file5')

xset('user.dir0', 'old extra value', CHKDIR / 'foo')
xset('user.dir1', 'old dir value', CHKDIR / 'foo')

xset('user.short', 'old short', CHKDIR / 'file1')
xset('user.extra', 'remove me', CHKDIR / 'file1')

xset('user.foo', 'old foo', CHKDIR / 'foo' / 'file3')
xset(f'{RUSR}.equal',
     'this long attribute should remain the same and not need to be transferred',
     CHKDIR / 'foo' / 'file3')

if hlink_variant:
    try:
        os.link(FROMDIR / 'foo' / 'bar' / 'file5', FROMDIR / 'foo' / 'bar' / 'file6')
    except OSError:
        test_skipped("Can't create hardlink")
    files.append('foo/bar/file6')
    dashH = ['-H']
    altDest = '--link-dest'
else:
    dashH = []
    altDest = '--copy-dest'


def _save_xattrs(paths, dest_file):
    """Snapshot the xattrs of `paths` (relative to cwd) into dest_file."""
    out = subprocess.check_output(['getfattr', '-d', *paths], text=True)
    dest_file.write_text(out)


_save_xattrs(dirs + files, SCRATCHDIR / 'xattrs.txt')

XFILT = ['-f-x_system.*', '-f-x_security.*']

# Simple xattr copy.
checkit(['-avX', *XFILT, *dashH, '--super', '.', f'{CHKDIR}/'], FROMDIR, CHKDIR)

os.chdir(CHKDIR)
got = subprocess.check_output(['getfattr', '-d', *(dirs + files)], text=True)
expected = (SCRATCHDIR / 'xattrs.txt').read_text()
if got != expected:
    from difflib import unified_diff
    sys.stdout.write(''.join(unified_diff(
        expected.splitlines(keepends=True),
        got.splitlines(keepends=True),
        fromfile='expected', tofile='got',
    )))
    test_fail("xattr listing differs after simple -X copy")

os.chdir(FROMDIR)

if dashH:
    for fn in files:
        name = os.path.basename(fn)
        os.link(fn, lnkdir / name)

checkit(['-aiX', *XFILT, *dashH, '--super', f'{altDest}=../chk', '.', '../to'],
        FROMDIR, TODIR)

os.chdir(TODIR)
got = subprocess.check_output(['getfattr', '-d', *(dirs + files)], text=True)
if got != expected:
    test_fail("xattr listing differs after --copy-dest / --link-dest copy")

if dashH:
    import shutil
    shutil.rmtree(lnkdir, ignore_errors=True)

os.chdir(FROMDIR)
import shutil
shutil.rmtree(TODIR, ignore_errors=True)

xset('user.nice', 'this is nice, but different', 'file1')

_save_xattrs(dirs + files, SCRATCHDIR / 'xattrs.txt')

checkit(['-aiX', *XFILT, *dashH, '--fake-super', '--link-dest=../chk', '.', '../to'],
        CHKDIR, TODIR)

os.chdir(TODIR)
got = subprocess.check_output(['getfattr', '-d', *(dirs + files)], text=True)
expected = (SCRATCHDIR / 'xattrs.txt').read_text()
if got != expected:
    test_fail("xattr listing differs after --fake-super --link-dest copy")

# Hard-link sanity for the hlink variant: file1 should be alone, every
# other file should share its inode with at least one peer.
if dashH:
    ls_to = SCRATCHDIR / 'ls-to'
    from rsyncfns import rsync_ls_lR
    ls_to.write_text(rsync_ls_lR(TODIR))
    one_link = []
    for line in ls_to.read_text().splitlines():
        # tls prints "mode size uid.gid links date time path" -- column
        # index 3 (zero-based) is the link count; we collect non-directory
        # entries whose link count is exactly 1.
        if line.startswith('d') or not line.strip():
            continue
        cols = line.split()
        if len(cols) >= 4 and cols[3] == '1':
            one_link.append(line)
    other_one = [ln for ln in one_link if './file1' not in ln]
    if other_one:
        print("Missing hard links on:")
        print('\n'.join(other_one))
        test_fail("hardlink check failed")
    if not one_link:
        test_fail("Too many hard links on file1!")

os.chdir(CHKDIR)
os.chmod('.', 0o700)
for p in dirs + files:
    os.chmod(p, os.stat(p).st_mode & ~0o077)

xset('user.nice', 'this is nice, but different', 'file1')
xset(f'{RSYNC_PREFIX}.%stat', f'40000 0,0 {uid_gid}', *dirs)
xset(f'{RSYNC_PREFIX}.%stat', f'100000 0,0 {uid_gid}', *files)

_save_xattrs(dirs + files, SCRATCHDIR / 'xattrs.txt')

os.chdir(FROMDIR)
shutil.rmtree(TODIR, ignore_errors=True)

# When run by a non-root tester, this verifies no-user-perm files/dirs
# can still be transferred.
checkit(['-aiX', *XFILT, *dashH, '--fake-super', '--chmod=a=', '.', '../to'],
        CHKDIR, TODIR)

os.chdir(TODIR)
got = subprocess.check_output(['getfattr', '-d', *(dirs + files)], text=True)
expected = (SCRATCHDIR / 'xattrs.txt').read_text()
if got != expected:
    test_fail("xattr listing differs after --fake-super --chmod=a= copy")

os.chdir(FROMDIR)
shutil.rmtree(TODIR, ignore_errors=True)
shutil.rmtree(CHKDIR, ignore_errors=True)

run_rsync('-aX', 'file1', 'file2')
run_rsync('-aX', 'file1', 'file2', '../chk/')
run_rsync('-aX', '--del', '../chk/', '.')
run_rsync('-aX', 'file1', '../lnk/')
if dashH:
    os.link(CHKDIR / 'file1', lnkdir / 'extra-link')

_save_xattrs(['file1', 'file2'], SCRATCHDIR / 'xattrs.txt')

checkit(['-aiiX', *XFILT, *dashH, f'{altDest}=../lnk', '.', '../to'],
        CHKDIR, TODIR)

if dashH:
    (lnkdir / 'extra-link').unlink()

os.chdir(TODIR)
got = subprocess.check_output(['getfattr', '-d', 'file1', 'file2'], text=True)
expected = (SCRATCHDIR / 'xattrs.txt').read_text()
if got != expected:
    test_fail("xattr listing differs after --link-dest=../lnk copy")

os.chdir(FROMDIR)
(TODIR / 'file2').unlink()

with open('file1', 'a') as f:
    f.write("extra\n")
run_rsync('-aX', '.', '../chk/')

checkit(['-aiiX', *XFILT, '.', '../to'], CHKDIR, TODIR)

os.chdir(TODIR)
got = subprocess.check_output(['getfattr', '-d', 'file1', 'file2'], text=True)
if got != expected:
    test_fail("xattr listing differs after the final round")

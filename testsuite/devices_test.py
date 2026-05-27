#!/usr/bin/env python3
# Python rewrite of testsuite/devices.test (and, via a Makefile-built
# symlink, of devices-fake.test).
#
# Test rsync's handling of device nodes (char/block/fifo) plus the
# fake-super variant that encodes device numbers in the
# user.rsync.%stat xattr instead of mknod-ing real devices.

import os
import subprocess
import sys

import rsyncfns
from rsyncfns import (
    CHKDIR, CHKFILE, FROMDIR, OUTFILE, RSYNC_PREFIX, TMPDIR, TODIR,
    all_plus, allspace, dots,
    checkdiff, hands_setup, makepath, rsync_ls_lR, run_rsync,
    test_fail, test_skipped, v_filt, xattr_set, xattrs_supported,
)


script_name = os.path.basename(sys.argv[0] if sys.argv[0] else __file__)
fake_variant = 'fake' in script_name

if fake_variant:
    if not xattrs_supported():
        test_skipped("Rsync needs xattrs for fake device tests")

    rsyncfns.RSYNC = rsyncfns.RSYNC + ' --fake-super'
    rsyncfns.TLS_ARGS = (rsyncfns.TLS_ARGS + ' --fake-super').strip()

    def make_special(path, kind: str, major: int = 0, minor: int = 0) -> bool:
        """Pretend to mknod `path` as kind {'p','c','b'} via rsync's
        fake-super "%stat" xattr (name/namespace handled per-OS by
        xattr_set). Returns True on success, False if the FS rejects it.
        """
        mode = {'p': 0o10644, 'c': 0o20644, 'b': 0o60644}[kind]
        try:
            with open(path, 'w'):
                pass
            xattr_set(f'{RSYNC_PREFIX}.%stat',
                      f"{mode:o} {major},{minor} 0:0", path)
            return True
        except (OSError, subprocess.CalledProcessError):
            return False
else:
    my_uid = os.getuid()
    if my_uid != 0:
        # Try fakeroot, mirroring the shell test.
        fakeroot_path = os.environ.get('FAKEROOT_PATH')
        if fakeroot_path and os.access(fakeroot_path, os.X_OK):
            print("Let's try re-running the script under fakeroot...")
            os.execv(fakeroot_path, [fakeroot_path, sys.executable, __file__])
        test_skipped("Rsync needs root/fakeroot for device tests")

    def make_special(path, kind: str, major: int = 0, minor: int = 0) -> bool:
        try:
            if kind == 'p':
                os.mkfifo(path)
            else:
                mode = 0o644 | (0o020000 if kind == 'c' else 0o060000)
                os.mknod(path, mode, os.makedev(major, minor))
            return True
        except OSError:
            return False


# Does this build of rsync support hard-linking specials?
vv = run_rsync('-VV', check=True, capture_output=True)
can_hlink_special = '"hardlink_specials": true' in vv.stdout

FROMDIR.mkdir(parents=True, exist_ok=True)
TODIR.mkdir(parents=True, exist_ok=True)

if not make_special(FROMDIR / 'char', 'c', 41, 67):
    test_skipped("Can't create char device node")
if not make_special(FROMDIR / 'char2', 'c', 42, 68):
    test_skipped("Can't create char device node")
if not make_special(FROMDIR / 'char3', 'c', 42, 69):
    test_skipped("Can't create char device node")
if not make_special(FROMDIR / 'block', 'b', 42, 69):
    test_skipped("Can't create block device node")
if not make_special(FROMDIR / 'block2', 'b', 42, 73):
    test_skipped("Can't create block device node")
if not make_special(FROMDIR / 'block3', 'b', 105, 73):
    test_skipped("Can't create block device node")

if can_hlink_special:
    try:
        os.link(FROMDIR / 'block3', FROMDIR / 'block3.5')
    except OSError:
        # The shell test prints a "Skipping hard-linked device test..." line
        # when it can't link the device; let it slide here, too.
        print("Skipping hard-linked device test... (link failed)")
        can_hlink_special = False
else:
    print("Skipping hard-linked device test...")

if not make_special(FROMDIR / 'fifo', 'p'):
    test_skipped("Can't run mkfifo")

# Match block/block2 timestamps so the diff doesn't drift.
ref = (FROMDIR / 'block').stat()
os.utime(FROMDIR / 'block', (ref.st_atime, ref.st_mtime), follow_symlinks=False)
os.utime(FROMDIR / 'block2', (ref.st_atime, ref.st_mtime), follow_symlinks=False)

checkdiff(['-ai', f'{FROMDIR}/block', f'{TODIR}/block2'],
          f"cD{all_plus} block\n")

checkdiff(['-ai', f'{FROMDIR}/block2', f'{TODIR}/block'],
          f"cD{all_plus} block2\n")

import time
time.sleep(1)

checkdiff(['-Di', f'{FROMDIR}/block3', f'{TODIR}/block'],
          f"cDc.T.{dots} block3\n")

# Build the expected -aiHvv listing.
chkfile_lines = [
    f".d..t.{dots} ./",
    f"cDc.t.{dots} block",
    f"cDc...{dots} block2",
    f"cD{all_plus} block3",
]
if can_hlink_special:
    chkfile_lines.append(f"hD{all_plus} block3.5 => block3")
chkfile_lines += [
    f"cD{all_plus} char",
    f"cD{all_plus} char2",
    f"cD{all_plus} char3",
    f"cS{all_plus} fifo",
]
expected = '\n'.join(chkfile_lines) + '\n'

checkdiff(['-aiHvv', f'{FROMDIR}/', f'{TODIR}/'], expected, filter=v_filt)

print("check how the directory listings compare with diff:\n")
ls_from = rsync_ls_lR(FROMDIR)
ls_to = rsync_ls_lR(TODIR)
if ls_from != ls_to:
    from difflib import unified_diff
    sys.stdout.write(''.join(unified_diff(
        ls_from.splitlines(keepends=True),
        ls_to.splitlines(keepends=True),
        fromfile='from', tofile='to',
    )))
    test_fail("from/to listings differ after device transfer")

if can_hlink_special:
    expected = (
        f"created directory {CHKDIR}\n"
        f"cd{allspace} ./\n"
        f"hD{allspace} block\n"
        f"hD{allspace} block2\n"
        f"hD{allspace} block3\n"
        f"hD{allspace} block3.5\n"
        f"hD{allspace} char\n"
        f"hD{allspace} char2\n"
        f"hD{allspace} char3\n"
        f"hS{allspace} fifo\n"
    )
    checkdiff(['-aii', f'--link-dest={TODIR}',
               f'{FROMDIR}/', f'{CHKDIR}/'], expected)

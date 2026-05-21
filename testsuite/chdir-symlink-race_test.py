#!/usr/bin/env python3
# Python rewrite of testsuite/chdir-symlink-race.test.
#
# Regression test for the symlink-TOCTOU bug at the receiver's chdir().
# Post-CVE-2026-29518 an attack remained where the receiver's chdir()
# into a destination subdirectory followed an attacker-planted symlink,
# escaping the module. Each of four transfer flavours must leave the
# outside-the-module sentinel's mode AND content unchanged.

import filecmp
import os
import platform
import subprocess

from rsyncfns import (
    SCRATCHDIR,
    get_rootgid, get_rootuid, get_testuid,
    make_data_file, rmtree, rsync_argv, start_test_daemon,
    test_fail, test_skipped,
)


DAEMON_PORT = 12885

if platform.system() in ('SunOS', 'OpenBSD', 'NetBSD') or platform.system().startswith('CYGWIN'):
    test_skipped(
        f"secure chdir relies on RESOLVE_BENEATH-equivalent kernel "
        f"support not available on {platform.system()}"
    )

mod = SCRATCHDIR / 'module'
outside = SCRATCHDIR / 'outside'
src = SCRATCHDIR / 'src_files'
conf = SCRATCHDIR / 'test-rsyncd.conf'

for d in (mod, outside, src):
    rmtree(d)
    d.mkdir(parents=True)
(src / 'subdir').mkdir()

# Secret sentinel; keep a pristine copy alongside for cmp(1)-style compares.
(outside / 'target.txt').write_text("OUTSIDE_SECRET_DATA\n")
os.chmod(outside / 'target.txt', 0o600)
outside_pristine = SCRATCHDIR / 'outside-pristine.txt'
import shutil
shutil.copy2(outside / 'target.txt', outside_pristine)

# Symlink trap planted by the local attacker.
os.symlink(str(outside), mod / 'subdir')

# Source files: same size as outside target, different content, mode 0666.
sz = (outside / 'target.txt').stat().st_size
make_data_file(src / 'target.txt', sz)
make_data_file(src / 'subdir' / 'target.txt', sz)
os.chmod(src / 'target.txt', 0o666)
os.chmod(src / 'subdir' / 'target.txt', 0o666)

conf.write_text(f"""\
use chroot = no
log file = {SCRATCHDIR}/rsyncd.log
[upload]
    path = {mod}
    use chroot = no
    read only = no
""")


def reset_outside() -> None:
    os.chmod(outside / 'target.txt', 0o600)
    (outside / 'target.txt').write_text("OUTSIDE_SECRET_DATA\n")
    os.chmod(outside / 'target.txt', 0o600)


def verify_unchanged(label: str) -> None:
    mode = (outside / 'target.txt').stat().st_mode & 0o777
    if mode != 0o600:
        test_fail(
            f"{label}: outside file mode changed from 600 to {oct(mode)[2:]} "
            "(chmod escape)"
        )
    if not filecmp.cmp(outside / 'target.txt', outside_pristine, shallow=False):
        test_fail(f"{label}: outside file content changed (write escape)")


url = start_test_daemon(conf, DAEMON_PORT)


def run_attack(label: str, *args) -> None:
    reset_outside()
    subprocess.run(
        rsync_argv(*args),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    verify_unchanged(label)


# 1. Single file with --size-only -- receiver normally skips basis open and
# goes straight to chmod; only the chdir-escape blocks it.
run_attack("single-file --size-only",
           '-tp', '--size-only',
           f'{src}/target.txt',
           f'{url}upload/subdir/target.txt')

# 2. -r push INTO the symlinked subdir -- receiver chdir's into "subdir",
# follows the symlink, ends up in outside.
run_attack("-r --size-only into subdir/",
           '-rtp', '--size-only',
           f'{src}/subdir/',
           f'{url}upload/subdir/')

# 3. Same but with delta+rename (read-disclosure + write-escape together).
run_attack("-r without --size-only into subdir/",
           '-rtp',
           f'{src}/subdir/',
           f'{url}upload/subdir/')

# 4. -r into the module root -- already covered by the original CVE fix;
# regression-check.
run_attack("-r --size-only into upload/ root",
           '-rtp', '--size-only',
           f'{src}/',
           f'{url}upload/')

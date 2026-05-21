#!/usr/bin/env python3
# Python rewrite of testsuite/bare-do-open-symlink-race.test.
#
# Codex audit Findings 3b, 3c-symlink and 3c-mknod: bare do_open /
# do_symlink / do_mknod paths on the receiver follow parent symlinks
# unrestrictedly.  Three scenarios are exercised; each must leave the
# outside-the-module sentinel unchanged.

import filecmp
import os
import platform
import shutil
import stat
import subprocess

from rsyncfns import (
    SCRATCHDIR,
    get_rootgid, get_rootuid, get_testuid,
    rmtree, rsync_argv, start_test_daemon, test_fail, test_skipped,
)


DAEMON_PORT = 12884


if platform.system() in ('SunOS', 'OpenBSD', 'NetBSD') or platform.system().startswith('CYGWIN'):
    test_skipped(
        f"secure_relative_open relies on RESOLVE_BENEATH-equivalent kernel "
        f"support not available on {platform.system()}"
    )

mod = SCRATCHDIR / 'module'
outside = SCRATCHDIR / 'outside'
src = SCRATCHDIR / 'src_files'
conf = SCRATCHDIR / 'test-rsyncd.conf'
outside_pristine = SCRATCHDIR / 'outside-pristine.txt'


def setup():
    for d in (mod, outside, src):
        rmtree(d)
        d.mkdir(parents=True)
    (outside / 'target.txt').write_text("OUTSIDE_PROTECTED_DATA\n")
    os.chmod(outside / 'target.txt', 0o644)
    shutil.copy2(outside / 'target.txt', outside_pristine)
    os.symlink(str(outside), mod / 'cd')


def verify_outside_unchanged(label: str) -> None:
    mode = (outside / 'target.txt').stat().st_mode & 0o777
    if mode != 0o644:
        test_fail(f"{label}: outside/target.txt mode changed from 644 to {oct(mode)[2:]}")
    if not filecmp.cmp(outside / 'target.txt', outside_pristine, shallow=False):
        test_fail(f"{label}: outside/target.txt content changed -- daemon followed the cd symlink")


def verify_outside_unchanged_or_absent(label: str, target: str) -> None:
    if (outside / target).exists() or (outside / target).is_symlink():
        test_fail(f"{label}: outside/{target} was created -- daemon followed the cd symlink")


my_uid = get_testuid()
root_uid = get_rootuid()
root_gid = get_rootgid()
uid_line = f"uid = {root_uid}"
gid_line = f"gid = {root_gid}"
if my_uid != root_uid:
    uid_line = '#' + uid_line
    gid_line = '#' + gid_line


# All three scenarios use the same daemon -- they just target a different
# module. Write both modules up-front so the daemon doesn't need to be
# restarted between scenarios.
conf.write_text(f"""\
use chroot = no
{uid_line}
{gid_line}
log file = {SCRATCHDIR}/rsyncd.log
[upload]
    path = {mod}
    use chroot = no
    read only = no

[upload_fake]
    path = {mod}
    use chroot = no
    read only = no
    fake super = yes
""")
daemon_url = start_test_daemon(conf, DAEMON_PORT).rstrip('/')


def run_attack(args):
    subprocess.run(
        rsync_argv(*args),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


# Scenario 3b: --inplace --backup --backup-dir=cd
setup()
(mod / 'target.txt').write_text("EXISTING_MODULE_DATA\n")
os.chmod(mod / 'target.txt', 0o666)
(src / 'target.txt').write_text("NEW_DATA_FROM_SENDER\n")
os.chmod(src / 'target.txt', 0o644)

run_attack([
    '--inplace', '--backup', '--backup-dir=cd',
    f'{src}/target.txt', f'{daemon_url}/upload/target.txt',
])
verify_outside_unchanged("3b inplace+backup-dir=cd")


# Scenario 3c-symlink: fake-super symlink push, parent-symlinked path
setup()
(src / 'cd').mkdir()
os.symlink('/etc/passwd', src / 'cd' / 'sym')

run_attack(['-rl', f'{src}/', f'{daemon_url}/upload_fake/'])
verify_outside_unchanged_or_absent("3c-symlink fake-super symlink push", "sym")


# Scenario 3c-mknod: fake-super FIFO push, parent-symlinked path
setup()
(src / 'cd').mkdir()
try:
    os.mkfifo(src / 'cd' / 'fifo')
except OSError:
    test_skipped("mkfifo unavailable; cannot exercise 3c-mknod")

if not stat.S_ISFIFO((src / 'cd' / 'fifo').stat().st_mode):
    test_skipped("mkfifo unavailable; cannot exercise 3c-mknod")

run_attack(['-rD', f'{src}/', f'{daemon_url}/upload_fake/'])
verify_outside_unchanged_or_absent("3c-mknod fake-super FIFO push", "fifo")

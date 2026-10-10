#!/usr/bin/env python3

import filecmp
import os
import shutil
import subprocess

from harness.rsync import SCRATCHDIR, make_data_file, rmtree, rsync_argv, start_test_daemon, test_fail
from harness import metadata

metadata(features={'daemon', 'symlink'}, transports={'pipe', 'tcp'}, min_peer='2.6.0', cost='expensive', mutates={'filesystem', 'process', 'socket'}, tags={'daemon', 'security', 'version-mix'})

DAEMON_PORT = 12885

mod = SCRATCHDIR / 'module'
outside = SCRATCHDIR / 'outside'
src = SCRATCHDIR / 'src_files'
conf = SCRATCHDIR / 'test-rsyncd.conf'

for d in (mod, outside, src):
    rmtree(d)
    d.mkdir(parents=True)
(src / 'subdir').mkdir()

(outside / 'target.txt').write_text("OUTSIDE_SECRET_DATA\n")
os.chmod(outside / 'target.txt', 0o600)
outside_pristine = SCRATCHDIR / 'outside-pristine.txt'
shutil.copy2(outside / 'target.txt', outside_pristine)

os.symlink(str(outside), mod / 'subdir')

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
    rc = subprocess.run(
        rsync_argv(*args),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    ).returncode
    if rc >= 128:
        test_fail(f"{label}: rsync died from a signal (rc={rc})")
    verify_unchanged(label)

def positive_control() -> None:
    real = mod / 'realdir'
    rmtree(real)
    real.mkdir()
    os.chmod(real, 0o777)
    rc = subprocess.run(
        rsync_argv(f'{src}/subdir/target.txt', f'{url}upload/realdir/'),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    ).returncode
    landed = real / 'target.txt'
    if rc != 0 or not landed.is_file() \
            or not filecmp.cmp(landed, src / 'subdir' / 'target.txt', shallow=False):
        test_fail(f"positive control: receiver did not write into an ordinary "
                  f"in-module subdir (rc={rc}); attack scenarios would be vacuous")

positive_control()

run_attack("single-file --size-only",
           '-tp', '--size-only',
           f'{src}/target.txt',
           f'{url}upload/subdir/target.txt')

run_attack("-r --size-only into subdir/",
           '-rtp', '--size-only',
           f'{src}/subdir/',
           f'{url}upload/subdir/')

run_attack("-r without --size-only into subdir/",
           '-rtp',
           f'{src}/subdir/',
           f'{url}upload/subdir/')

run_attack("-r --size-only into upload/ root",
           '-rtp', '--size-only',
           f'{src}/',
           f'{url}upload/')

#!/usr/bin/env python3

import os
import subprocess

from harness.rsync import (
    SCRATCHDIR, find_attacker_uid,
    rmtree, rsync_argv, start_test_daemon, test_fail, test_skipped, write_daemon_conf,
)

if os.geteuid() != 0:
    test_skipped("requires root to plant a backup-dir symlink owned by a non-self uid",
                 capability='cross_uid')
ATT_UID = find_attacker_uid()
if ATT_UID is None:
    test_skipped("no untrusted-uid user available for cross-uid plant", capability='cross_uid')

DAEMON_PORT = 12903
OLD = "INSIDE_MODULE_DATA\n"
NEW = "NEW_PUSHED_DATA\n"

mod = SCRATCHDIR / 'module'
outside = SCRATCHDIR / 'outside'
src = SCRATCHDIR / 'src_files'
for d in (mod, outside, src):
    rmtree(d)
    d.mkdir(parents=True)

(mod / 'foo').write_text(OLD)

(src / 'foo').write_text(NEW)

conf = write_daemon_conf([
    ('upload', {'path': str(mod), 'use chroot': 'no', 'read only': 'no',
                'uid': '0', 'gid': '0'}),
])
daemon_url = start_test_daemon(conf, DAEMON_PORT).rstrip('/')

def push_with_backup():
    return subprocess.run(
        rsync_argv('-t', '--backup', '--backup-dir=bdir',
                   f'{src}/foo', f'{daemon_url}/upload/'),
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

(mod / 'bdir').mkdir()
proc = push_with_backup()
if proc.returncode != 0:
    test_fail("positive control: daemon --backup-dir transfer failed "
              f"(rc={proc.returncode}):\n{proc.stdout or ''}")
backup = mod / 'bdir' / 'foo'
if not backup.is_file():
    test_fail("positive control: --backup-dir=bdir did not create "
              f"{backup}; the test would not exercise the backup rename path")
if backup.read_text() != OLD:
    test_fail("positive control: backup content differs from the original "
              f"module/foo content: {backup.read_text()!r}")
if (mod / 'foo').read_text() != NEW:
    test_fail("positive control: module/foo was not overwritten by the "
              "daemon transfer")

rmtree(mod / 'bdir')
(mod / 'foo').write_text(OLD)

os.symlink(str(outside), mod / 'bdir')
os.lchown(mod / 'bdir', ATT_UID, ATT_UID)

proc = push_with_backup()
attack_out = proc.stdout or ''

if os.path.lexists(outside / 'foo'):
    try:
        got = (outside / 'foo').read_text().strip()
    except OSError:
        got = '<unreadable>'
    test_fail("mixed-parent rename escaped the module: backup of module/foo "
              f"landed in {outside}/foo (content: {got}); do_rename_at "
              "followed the planted backup-dir symlink")

leaked = os.listdir(outside)
if leaked:
    test_fail(f"unexpected files escaped the module into {outside}: {leaked}")

try:
    final = (mod / 'foo').read_text()
except (OSError, UnicodeError) as exc:
    test_fail("foreign-owned --backup-dir symlink transfer left module/foo "
              f"unreadable ({exc!r}; rc={proc.returncode}):\n{attack_out}")
if final == NEW:
    test_fail("foreign-owned --backup-dir symlink transfer overwrote module/foo "
              "without taking or refusing the backup through bdir; the "
              f"--backup-dir path may not have been exercised "
              f"(rc={proc.returncode}):\n{attack_out}")

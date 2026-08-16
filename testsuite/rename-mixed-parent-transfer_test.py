#!/usr/bin/env python3
# Python rewrite of testsuite/rename-mixed-parent-transfer.test.
#
# End-to-end (real daemon transfer) companion to the unit-level
# rename-mixed-parent-symlink-race / rename-mixed-parent-escape-poc tests.
#
# Those tests exercise do_rename_at() directly. This one drives the escape
# through an actual rsync transfer against a no-chroot daemon, to prove the
# 3.4.3 do_rename_at() mixed-parent fallback is reachable and exploitable
# from the wire, not just at the function boundary.
#
# Background: do_rename_at() in 3.4.3 only confined parent resolution under
# secure_relative_open() when *both* the source and destination contained a
# slash:
#
#     if (!old_slash || !new_slash)
#             return do_rename(old_path, new_path);  /* plain rename() */
#
# So a rename where one side is a bare top-of-module name (resolved in the
# module-root CWD, which is safe) and the other side is slashed (whose parent
# the kernel resolves freely) fell back to plain rename() -- letting an
# attacker-planted parent symlink on the slashed side escape the module.
#
# Trigger: --backup --backup-dir=bdir. When the daemon overwrites an existing
# top-of-module file "foo", it backs it up via make_backup() -> link_or_rename(),
# ending in do_rename_at("foo", "bdir/foo"): source bare, destination slashed.
# With "bdir" planted as a symlink pointing outside the module, the 3.4.3
# fallback renames the in-module file out through the symlink. With the
# per-side fix, the slashed destination parent is opened under
# secure_relative_open(), the symlink is refused, and the file stays inside.
# (do_link_at() is tried first but already has the per-side hardening, so it
# refuses the symlinked backup dir and falls through to do_rename_at().)
#
# The vulnerable code path is the same on every platform (including the
# per-component fallback on systems without RESOLVE_BENEATH), so this test is
# not platform-gated.

import os
import subprocess

from rsyncfns import (
    SCRATCHDIR, find_attacker_uid,
    rmtree, rsync_argv, start_test_daemon, test_fail, test_skipped, write_daemon_conf,
)

# Operator paths follow a uid0/euid-owned symlink and refuse a foreign-owned one,
# so the escape vector is an ATTACKER-owned backup-dir symlink and a root daemon.
if os.geteuid() != 0:
    test_skipped("requires root to plant a backup-dir symlink owned by a non-self uid")
ATT_UID = find_attacker_uid()
if ATT_UID is None:
    test_skipped("no untrusted-uid user available for cross-uid plant")


DAEMON_PORT = 12903
OLD = "INSIDE_MODULE_DATA\n"
NEW = "NEW_PUSHED_DATA\n"

mod = SCRATCHDIR / 'module'
outside = SCRATCHDIR / 'outside'
# NB: 'src' is reserved by runtests.py (a symlink to the source tree), so use
# a distinct name for our push source.
src = SCRATCHDIR / 'src_files'
for d in (mod, outside, src):
    rmtree(d)
    d.mkdir(parents=True)

# The existing in-module file the daemon will overwrite (and thus back up).
# Its content is what an attacker would relocate outside the module.
(mod / 'foo').write_text(OLD)

# A different source so the push genuinely overwrites foo and triggers backup.
(src / 'foo').write_text(NEW)

# Serve the module as root so the daemon euid differs from the attacker uid; the
# ownership walk must then refuse the foreign-owned backup-dir symlink.  Use
# numeric uid/gid 0, not the name "root": the root *group* is "wheel" on the BSDs
# and macOS, so `gid = root` is rejected there.
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


# Positive control: a real backup-dir must receive the original file, proving
# this transfer shape exercises --backup-dir and the mixed-parent backup rename.
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

# The attacker-planted backup-dir symlink, pointing outside the module, owned by
# the attacker uid (not root, the uid the module is served as).
os.symlink(str(outside), mod / 'bdir')
os.lchown(mod / 'bdir', ATT_UID, ATT_UID)

# Push the changed file straight into the module root with backups enabled and
# a backup-dir relative to the module root. The transfer may report an error on
# the fixed binary (the secure backup rename is refused), so the security oracle
# is where the files end up.
proc = push_with_backup()
attack_out = proc.stdout or ''

# The escape: the original in-module file was renamed out through the bdir
# symlink into the outside-the-module directory.
if os.path.lexists(outside / 'foo'):
    try:
        got = (outside / 'foo').read_text().strip()
    except OSError:
        got = '<unreadable>'
    test_fail("mixed-parent rename escaped the module: backup of module/foo "
              f"landed in {outside}/foo (content: {got}); do_rename_at "
              "followed the planted backup-dir symlink")

# Sanity: nothing else should have leaked outside the module either.
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

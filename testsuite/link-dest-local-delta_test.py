#!/usr/bin/env python3
# Regression test for the wider half of the issue #915 / CVE-2026-29518
# fallout: the receiver's alt-dest basis open (secure_basis_open in
# receiver.c) was routed unconditionally through secure_relative_open(),
# whose front door rejects a relative ".." basedir.  That broke the
# DELTA path for --link-dest=../01 (a sibling backup) in every context
# where the basis is opened for reading, not just the no-chroot daemon
# that issue #915 reported:
#
#   * local / remote-shell transfers with --no-whole-file (delta on), and
#   * "use chroot = yes" daemons (the default), where the chroot is the
#     boundary and module_dirlen is 0 so the path reaches the receiver as
#     a literal "../01".
#
# Symptom was not a graceful fallback but a hard abort:
#   "got a block match with no basis file ... protocol incompatibility".
# The sender deltas the changed file against the basis, the receiver
# can't open the basis, and receive_data() dies.
#
# The fix gives secure_basis_open() the same gate the do_*_at() wrappers
# use (bare open unless am_daemon && !am_chrooted), so the basis resolves
# against the CWD exactly as a pre-CVE bare open did.  Pre-CVE 3.4.1
# worked here; this guards the repair.
#
# This test covers the LOCAL --no-whole-file path (no root needed).  The
# chroot-daemon path is the same code with am_chrooted=1; it needs real
# root to chroot() and so isn't exercised here (the no-chroot daemon is
# covered by link-dest-daemon_test.py).

import os
import subprocess

from rsyncfns import SCRATCHDIR, rsync_argv, rmtree, test_fail


work = SCRATCHDIR / 'lddelta'
rmtree(work)
(work / '01').mkdir(parents=True)   # the sibling "previous backup"
(work / '00').mkdir(parents=True)   # the destination subdir
(work / 'src').mkdir(parents=True)  # the source

# A large file so rsync genuinely deltas (most blocks match the basis,
# one byte differs).  Distinct mtimes so the quick-check does NOT treat
# them as identical -- that would hard-link instead of exercising the
# delta/basis-open path we care about.
basis = work / '01' / 'f'
src = work / 'src' / 'f'
basis.write_text('A' * 200000 + 'X')
src.write_text('A' * 200000 + 'Y')
os.utime(basis, (1_000_000_000, 1_000_000_000))  # 2001
os.utime(src, (1_700_000_000, 1_700_000_000))    # 2023

# Run from `work` so the dest "00/" and the relative "--link-dest=../01"
# resolve as siblings -- the rotating-backup layout.  --no-whole-file
# forces the delta transfer (local transfers default to whole-file).
proc = subprocess.run(
    rsync_argv('-rt', '--no-whole-file', '--link-dest=../01', 'src/', '00/'),
    cwd=str(work), stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
)
if proc.returncode != 0:
    test_fail(
        "local --link-dest=../01 delta aborted (rc="
        f"{proc.returncode}): the receiver could not open the sibling basis "
        "for the delta transfer -- secure_basis_open wrongly confined a "
        f"relative '..' basis outside the daemon context.  stderr:\n{proc.stderr}")

dest = work / '00' / 'f'
if not dest.is_file():
    test_fail("local --link-dest=../01 delta produced no destination file")
if dest.read_text() != src.read_text():
    test_fail(
        "local --link-dest=../01 delta reconstructed the wrong content: the "
        "destination does not match the source after a delta against the "
        "sibling basis")

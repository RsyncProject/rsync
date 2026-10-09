#!/usr/bin/env python3
# KI-72: --safe-links must be honored in the backup symlink hard-link path.
# Verifies: SW-REQ-073
#
# In make_backup_inner(), when CAN_HARDLINK_SYMLINK is defined (Linux, macOS)
# the hard-link fast path link_or_rename() succeeded for a symlink and then
# "goto success" skipped the safe_symlinks check that lives in the copy-fallback
# path.  The net effect was that an escaping symlink already present in the
# destination (e.g. "escape -> ../../sensitive") was silently preserved in the
# backup area (as "<name>.bak") even when --safe-links was given.
#
# This forces exactly that backup (a destination symlink is replaced by a
# regular file from the source) and asserts:
#   * WITH    --safe-links : the escaping symlink is NOT backed up (no .bak).
#   * WITHOUT --safe-links : the escaping symlink IS backed up (.bak is a
#                            symlink with the same escaping target).
#
# Pure local client behaviour: no daemon/root/tcp. RED on the unfixed tree on
# any platform whose link_or_rename() can hard-link a symlink (CAN_HARDLINK_SYMLINK);
# GREEN everywhere once the check runs before the fast path.

import os
import subprocess

from rsyncfns import SCRATCHDIR, rmtree, rsync_argv, test_fail

base = SCRATCHDIR / 'ki72-safe-links-backup'

# The escaping target: at the transfer top level the symlink path is depth 0,
# so any leading "../" makes unsafe_symlink() reject it.  Use two levels to be
# unambiguously out-of-tree regardless of how the receiver names the leaf.
ESCAPE_TARGET = '../../sensitive'


def build():
    """Fresh src/ and dest/ where the next sync must back up dest's escaping
    symlink (src carries a regular file of the same name that replaces it)."""
    rmtree(base)
    src = base / 'src'
    dest = base / 'dest'
    src.mkdir(parents=True)
    dest.mkdir(parents=True)
    (src / 'keep.txt').write_text('source regular file\n')
    # A regular file at src that differs from dest's symlink -> forces an update
    # and therefore a backup of the old dest entry.
    (src / 'escape').write_text('replacement regular file\n')
    # The pre-existing escaping symlink in the destination that gets backed up.
    os.symlink(ESCAPE_TARGET, dest / 'escape')
    return src, dest


def run(src, dest, *extra):
    proc = subprocess.run(
        rsync_argv('-a', '--backup', '--suffix=.bak', *extra,
                   f'{src}/', f'{dest}/'),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    return proc


# ---------------------------------------------------------------------------
# 1. WITH --safe-links: the unsafe symlink must NOT be backed up.
# ---------------------------------------------------------------------------
src, dest = build()
proc = run(src, dest, '--safe-links')
if proc.returncode != 0:
    test_fail("KI-72: rsync exited %d with --safe-links\n%s%s"
              % (proc.returncode, proc.stdout, proc.stderr))

bak = dest / 'escape.bak'
if os.path.lexists(bak):
    test_fail(
        "KI-72: --safe-links was bypassed on the backup hard-link path: the "
        "escaping symlink was backed up to %s (target %r) instead of being "
        "dropped." % (bak, os.readlink(bak) if os.path.islink(bak) else '?'))

# The replacement regular file must still have been transferred.
if not (dest / 'escape').is_file():
    test_fail("KI-72: the replacement regular file 'escape' was not transferred")


# ---------------------------------------------------------------------------
# 2. WITHOUT --safe-links: the escaping symlink IS backed up (positive
#    control, so the assertion above isn't vacuous -- backups really happen).
# ---------------------------------------------------------------------------
src, dest = build()
proc = run(src, dest)
if proc.returncode != 0:
    test_fail("KI-72: rsync exited %d without --safe-links\n%s%s"
              % (proc.returncode, proc.stdout, proc.stderr))

bak = dest / 'escape.bak'
if not os.path.islink(bak):
    test_fail("KI-72: without --safe-links the escaping symlink should have "
              "been backed up, but %s is missing or not a symlink." % bak)
if os.readlink(bak) != ESCAPE_TARGET:
    test_fail("KI-72: backup symlink points to %r, expected %r"
              % (os.readlink(bak), ESCAPE_TARGET))

# ---------------------------------------------------------------------------
# 3. WITH --safe-links, a SAFE (in-tree) symlink must STILL be backed up.
#    The fix skips the backup for an unsafe OR unreadable link; this guards
#    against it over-blocking and dropping legitimate safe symlinks too.
# ---------------------------------------------------------------------------
rmtree(base)
src = base / 'src'
dest = base / 'dest'
src.mkdir(parents=True)
dest.mkdir(parents=True)
(src / 'keep.txt').write_text('source regular file\n')
(src / 'link').write_text('replacement regular file\n')
# An in-tree (safe) symlink in dest that gets replaced -> must be backed up.
os.symlink('keep.txt', dest / 'link')

proc = run(src, dest, '--safe-links')
if proc.returncode != 0:
    test_fail("KI-72: rsync exited %d backing up a safe symlink\n%s%s"
              % (proc.returncode, proc.stdout, proc.stderr))

bak = dest / 'link.bak'
if not os.path.islink(bak):
    test_fail("KI-72: a SAFE symlink was wrongly dropped from the backup area "
              "under --safe-links (%s missing or not a symlink) -- the fix is "
              "over-blocking." % bak)
if os.readlink(bak) != 'keep.txt':
    test_fail("KI-72: safe backup symlink points to %r, expected 'keep.txt'"
              % os.readlink(bak))

print("ki72-safe-links-backup: --safe-links drops an escaping symlink from the "
      "backup area, preserves a safe one, while a plain --backup preserves both")

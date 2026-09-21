#!/usr/bin/env python3
# rrsync (the SSH-restricted-rsync wrapper) must close its realpath-vs-exec
# TOCTOU window so an attacker cannot flip a path component after rrsync's
# validation but before the spawned rsync resolves it.
#
# Threat: support/rrsync does
#       real_arg = os.path.realpath(arg)
#       if arg != real_arg and not real_arg.startswith(args.dir_slash):
#           die('unsafe arg:', ...)
# and then runs rsync against the same arg string. Between realpath() and the
# spawned rsync's resolution of the path, an attacker can flip a component from
# a real entry (which passes realpath) to a symlink escaping the restricted
# dir. rsync then transfers data from / to outside the restricted dir.
#
# This is a TOCTOU race; static plants are caught by realpath. The fix pins the
# validated inode with os.open(O_RDONLY|O_NOFOLLOW) + os.fstat() during
# validation and hands rsync a name rooted at that pinned fd, so the resolution
# is atomic with the check.
#
# WHAT THIS TEST FLIPS, AND WHY IT IS A DIRECTORY COMPONENT
#
# rrsync pins every component ABOVE the argument's last one, in both
# directions, and that is what this exercises: an intermediate directory is
# swapped for a symlink pointing outside the tree.
#
# It deliberately does not flip the LAST component, because rrsync does not
# always pin that one and the stub below could not judge it if it did:
#   * a receiver destination that does not exist yet has no inode to pin, so
#     the leaf is necessarily still resolved by name -- that is what creating a
#     file means;
#   * a sender source cannot be handed a /proc/self/fd magic link at all: rsync
#     lstat()s a source argument, sees S_IFLNK, and sends a symlink instead of
#     the file (see rrsync-pull-delivers-content).
# Leaf behaviour is therefore asserted against the real binary in
# rrsync-sender-leaf-flip, which can observe what rsync actually does with a
# raced leaf rather than what this stub assumes.
#
# Sender direction only. The receiver has a residual window this cannot yet
# assert: when the destination leaf does not exist -- including the instant
# between the flipper's two renames -- rrsync falls back to pinning the parent,
# and if THAT open also loses the race it passes the argument through unpinned.
# Measured over a 5s race, this stub's "rsync" reached the outside marker 8
# times as a sender and 11 as a receiver against a pristine 3.4.4 rrsync; with
# the inode pin it is 0 as a sender and still 2 as a receiver, on the branch
# base as well as with the sender fix. The receiver fallback predates this
# change and is tracked separately.
#
# Test machinery:
#   * The system rrsync hardcodes RSYNC = '/usr/bin/rsync'; we copy it
#     to scratch and patch RSYNC to a tiny "fake rsync" stub whose only
#     job is to open its last argv (the validated path) and emit its
#     content. The race signal is whether the fake rsync ever emits the
#     attacker's outside-the-tree secret. For an intermediate component the
#     stub is a faithful model: whatever rsync does with the final name, it
#     must not be able to reach it through a flipped parent.
#   * A flipper subprocess (rsyncfns.start_path_flipper) swaps the
#     restricted dir's 'dir' name between (a) a real directory and (b) a
#     symlink to the outside tree. The flip is rename-based and atomic.
#   * We loop rrsync invocations for up to RACE_TIMEOUT seconds; observing
#     the secret marker in ANY iteration's stdout is the leak signal.
#
# Runs at any uid (the race is timing-only, not ownership-based).

import os
import subprocess
import time

from rsyncfns import (
    race_budget, SCRATCHDIR, patched_rrsync, proc_self_fd_pins, rmtree,
    start_path_flipper, stop_flipper, test_fail, test_skipped,
)

# rrsync closes this realpath-vs-exec TOCTOU by inode-pinning through
# /proc/self/fd (Linux-only).  Where that primitive is absent (the BSDs, Solaris,
# macOS, Cygwin) rrsync falls through unpinned by design, so the race is not
# closed and this test cannot pass -- skip rather than report the intended gap.
if not proc_self_fd_pins():
    test_skipped("rrsync's realpath-vs-exec inode-pin needs /proc/self/fd "
                 "(Linux); unhardened fallback elsewhere by design")


MARKER = b"AUDIT_RRSYNC_RACE_LEAK_MARKER_DO_NOT_DISCLOSE"

base = SCRATCHDIR / 'rrsync_race'
restricted = base / 'restricted'
outside = base / 'outside'
rmtree(base)
restricted.mkdir(parents=True)
outside.mkdir(parents=True)

# Same leaf name on both sides, so the ONLY thing that decides which file the
# spawned rsync reaches is which inode the 'dir' component resolved to.
(outside / 'target').write_bytes(MARKER)

# Two sibling names in the restricted dir that the flipper swaps, so the 'dir'
# component alternates between a real directory and a symlink escaping the
# tree.  We use distinct setup names that the flipper then rotates -- the same
# shape symlink-race-source/dest use.
real_dir = restricted / 'dir'
real_dir.mkdir()
(real_dir / 'target').write_bytes(b"benign_in_tree_content\n")
evil_path = restricted / 'evil'
os.symlink(str(outside), evil_path)

# Fake rsync stub: prints the bytes at its last argv (the path rrsync passed).
fake_rsync = base / 'fake_rsync'
fake_rsync.write_text(
    "#!/usr/bin/env python3\n"
    "import sys\n"
    "try:\n"
    "    with open(sys.argv[-1], 'rb') as f:\n"
    "        sys.stdout.buffer.write(f.read())\n"
    "except OSError:\n"
    "    pass\n"
)
fake_rsync.chmod(0o755)

# Patched rrsync: RSYNC → fake_rsync (via the shared, path-robust helper).
test_rrsync = patched_rrsync(base, rsync_path=str(fake_rsync))


def race(rrsync_flags, ssh_cmd):
    """Run rrsync in a loop against the flipping tree; True if the secret leaked."""
    env = {**os.environ, 'SSH_ORIGINAL_COMMAND': ssh_cmd}
    deadline = time.monotonic() + race_budget()
    while time.monotonic() < deadline:
        proc = subprocess.run(
            [str(test_rrsync), *rrsync_flags, '-no-lock', str(restricted)],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env)
        if MARKER in proc.stdout:
            return True
    return False


flip = start_path_flipper(real_dir, evil_path)
try:
    leaked = race(['-ro'], "rsync --server --sender -lt . dir/target")
finally:
    stop_flipper(flip)

if leaked:
    test_fail(
        "rrsync realpath-vs-exec TOCTOU: an intermediate path component "
        "(real directory -> symlink to the outside tree) was followed by the "
        f"spawned rsync; the secret marker {MARKER!r} was emitted, proving "
        "the path was resolved AFTER rrsync's realpath check validated it. "
        "Fix: pin the validated inode with os.open(O_RDONLY|O_NOFOLLOW) + "
        "os.fstat() during validation, then root the argument handed to rsync "
        "at that pinned fd.")

print('rrsync pins intermediate sender path components against the realpath-vs-exec race')

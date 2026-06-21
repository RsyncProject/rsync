#!/usr/bin/env python3
# rrsync (the SSH-restricted-rsync wrapper) must close its realpath-vs-exec
# TOCTOU window so an attacker cannot flip a path component after rrsync's
# validation but before the spawned rsync resolves it.
#
# Threat: support/rrsync line 329
# does
#       real_arg = os.path.realpath(arg)
#       if arg != real_arg and not real_arg.startswith(args.dir_slash):
#           die('unsafe arg:', ...)
# then exec's rsync against the same arg string. Between realpath() and the
# exec'd rsync's open of the path, an attacker can flip a component from a
# real entry (passes realpath) to a symlink escaping the restricted dir.
# rsync then transfers data from / to outside the restricted dir.
#
# This is a TOCTOU race; static plants are caught by realpath. The fix
# uses per-component os.open(O_PATH|O_NOFOLLOW) + os.fstat() to pin the
# inode during validation and exec rsync against /proc/self/fd/N (or
# revalidate after fork) so the resolution is atomic with the check.
#
# Test machinery:
#   * The system rrsync hardcodes RSYNC = '/usr/bin/rsync'; we copy it
#     to scratch and patch RSYNC to a tiny "fake rsync" stub whose only
#     job is to open its last argv (the validated path) and emit its
#     content. The race signal is whether the fake rsync ever emits the
#     attacker's outside-the-tree secret.
#   * A flipper subprocess (rsyncfns.start_path_flipper) swaps the
#     restricted dir's 'target' name between (a) a real file and (b) a
#     symlink to outside/secret. The flip is rename-based and atomic.
#   * We loop rrsync invocations for up to RACE_TIMEOUT seconds; observing
#     the secret marker in ANY iteration's stdout is the leak signal.
#
# Runs at any uid (the race is timing-only, not ownership-based).

import os
import shlex
import subprocess
import time
from pathlib import Path

from rsyncfns import (
    RACE_TIMEOUT, RSYNC, SCRATCHDIR, proc_self_fd_pins, rmtree,
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

# The attacker-outside secret rsync would never legitimately reach.
(outside / 'secret').write_bytes(MARKER)

# Two sibling names in the restricted dir that the flipper will swap so the
# 'target' name alternates between regular file and escaping symlink. We use
# distinct setup names that the flipper then rotates -- consistent with
# how symlink-race-source/dest use start_path_flipper.
real_path = restricted / 'target'
real_path.write_bytes(b"benign_in_tree_content\n")
evil_path = restricted / 'evil'
os.symlink(str(outside / 'secret'), evil_path)

# Fake rsync stub: prints the bytes at its last argv (the path rrsync passed).
# Mirrors the real rsync semantic of "follow the path it was given".
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

# Patched rrsync: RSYNC → fake_rsync.
src_rrsync = Path(__file__).resolve().parent.parent / 'support' / 'rrsync'
test_rrsync = base / 'test_rrsync'
test_rrsync.write_text(
    src_rrsync.read_text().replace(
        "RSYNC = '/usr/bin/rsync'",
        f"RSYNC = {str(fake_rsync)!r}",
        1,
    )
)
test_rrsync.chmod(0o755)


flip = start_path_flipper(real_path, evil_path)
ssh_cmd = "rsync --server --sender -lt . target"
env = {**os.environ, 'SSH_ORIGINAL_COMMAND': ssh_cmd}

won = False
deadline = time.monotonic() + RACE_TIMEOUT
try:
    while time.monotonic() < deadline:
        proc = subprocess.run(
            [str(test_rrsync), '-ro', '-no-lock', str(restricted)],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            env=env)
        if MARKER in proc.stdout:
            won = True
            break
finally:
    stop_flipper(flip)

if won:
    test_fail(
        "rrsync realpath-vs-exec TOCTOU: a flipped path component "
        "(real file -> symlink to outside/secret) was followed by the "
        f"exec'd rsync; the secret marker {MARKER!r} was emitted, "
        "proving rsync resolved the path AFTER rrsync's realpath check "
        "validated it. Fix: per-component os.open(O_PATH|O_NOFOLLOW) "
        "+ os.fstat() inode pinning during validation, then exec rsync "
        "against /proc/self/fd/N (or revalidate after fork).")

#!/usr/bin/env python3
# A subdir-restricted rrsync must accept a normal `rsync -a` transfer.
#
# A/B-discovered regression (abdiff: 3.5 rrsync vs 3.4.4 rrsync).  3.5 added 'D'
# to rrsync's short_disabled_subdir ('KLk' -> 'DKLk') to deny device/special
# CREATION in a restricted upload dir.  But `-a` is `-rlptgoD`: the client always
# sends a bundled short-option string containing 'D' (e.g. -logDtpre.iLsfxCIvu),
# so a subdir-restricted rrsync now REJECTS plain `rsync -a` outright:
#     <rrsync> error: option -D has been disabled on this server.   (exit 1)
# -- in BOTH directions (push and pull).  3.4.4's rrsync accepted it.  This
# breaks the canonical "rsync -a ... rrsync-host:dir/" backup for every restricted
# (non-"/") rrsync, on every platform.  The deny should strip device/special
# semantics (e.g. force --no-D), not reject the whole transfer.
#
# Stub test (RSYNC -> true): we only check rrsync's option ACCEPTANCE, not a real
# transfer.  Runs anywhere python3 + true exist; no root.

import os
import shutil
import subprocess

from rsyncfns import SCRATCHDIR, patched_rrsync, test_fail, test_skipped

true = shutil.which('true') or '/usr/bin/true'
if not os.path.exists(true):
    test_skipped("no true(1) to stub rsync")

base = SCRATCHDIR / 'rrsync_a'
restricted = base / 'restricted'
restricted.mkdir(parents=True, exist_ok=True)
# The pull (--sender) command reads `inbox/`; rrsync inode-pins an existing
# --sender source before exec (a non-existent one is treated as a race and
# refused), so the source must exist for this option-acceptance check.
(restricted / 'inbox').mkdir(exist_ok=True)
rrsync = patched_rrsync(base, rsync_path=true)

# What a stock `rsync -a` sends as the server command (bundled short opts incl D):
for direction, cmd in (("push", 'rsync --server -logDtpre.iLsfxCIvu . inbox/'),
                       ("pull", 'rsync --server --sender -logDtpre.iLsfxCIvu . inbox/')):
    env = {**os.environ, 'SSH_ORIGINAL_COMMAND': cmd}
    r = subprocess.run([str(rrsync), str(restricted)], env=env,
                       capture_output=True, text=True)
    if r.returncode != 0:
        test_fail(f"subdir-restricted rrsync rejected `rsync -a` ({direction}, "
                  f"exit {r.returncode}): {r.stderr.strip()}")

print("rrsync-archive-mode: subdir-restricted rrsync accepts `rsync -a` (push & pull)")

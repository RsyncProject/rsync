#!/usr/bin/env python3

import os
import shutil
import subprocess

from harness.rsync import SCRATCHDIR, patched_rrsync, test_fail, test_skipped

true = shutil.which('true') or '/usr/bin/true'
if not os.path.exists(true):
    test_skipped("no true(1) to stub rsync")

base = SCRATCHDIR / 'rrsync_a'
restricted = base / 'restricted'
restricted.mkdir(parents=True, exist_ok=True)
(restricted / 'inbox').mkdir(exist_ok=True)
rrsync = patched_rrsync(base, rsync_path=true)

for direction, cmd in (("push", 'rsync --server -logDtpre.iLsfxCIvu . inbox/'),
                       ("pull", 'rsync --server --sender -logDtpre.iLsfxCIvu . inbox/')):
    env = {**os.environ, 'SSH_ORIGINAL_COMMAND': cmd}
    r = subprocess.run([str(rrsync), str(restricted)], env=env,
                       capture_output=True, text=True)
    if r.returncode != 0:
        test_fail(f"subdir-restricted rrsync rejected `rsync -a` ({direction}, "
                  f"exit {r.returncode}): {r.stderr.strip()}")

print("rrsync-archive-mode: subdir-restricted rrsync accepts `rsync -a` (push & pull)")

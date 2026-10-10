#!/usr/bin/env python3

import os
import subprocess

from harness.rsync import SCRATCHDIR, TOOLDIR, rmtree, test_fail

mod = SCRATCHDIR / 'module'
trap_outside = SCRATCHDIR / 'trap'
rmtree(mod)
rmtree(trap_outside)
mod.mkdir(parents=True)
(mod / 'realdir').mkdir(parents=True)
trap_outside.mkdir(parents=True)

(mod / 'realdir' / 'sentinel').write_text("bystander\n")
os.chmod(mod / 'realdir' / 'sentinel', 0o600)
(trap_outside / 'sentinel').write_text("target\n")
os.chmod(trap_outside / 'sentinel', 0o600)
os.symlink('realdir', mod / 'inside_link')
os.symlink('../trap', mod / 'escape_link')
(mod / 'topfile').write_text("top\n")
os.chmod(mod / 'topfile', 0o600)
os.symlink('../../trap/sentinel', mod / 'realdir' / 'leaflink')

proc = subprocess.run([str(TOOLDIR / 't_chmod_secure'), str(mod)])
if proc.returncode != 0:
    test_fail("t_chmod_secure reported failures (see stderr above)")

sentinel_mode = (trap_outside / 'sentinel').stat().st_mode & 0o777
if sentinel_mode != 0o600:
    test_fail(
        f"outside sentinel mode changed from 600 to {oct(sentinel_mode)[2:]} "
        "-- chmod escaped the module"
    )

#!/usr/bin/env python3

import os
import subprocess

from harness.rsync import (
    SCRATCHDIR, TOOLDIR,
    assert_exists, assert_not_exists, rmtree, test_fail, test_skipped,
)

mod = SCRATCHDIR / 'module'
trap_outside = SCRATCHDIR / 'trap'
rmtree(mod)
rmtree(trap_outside)
(mod / 'realdir').mkdir(parents=True)
trap_outside.mkdir(parents=True)

(mod / 'top-to-dir').write_text("top-to-dir\n")
(mod / 'realdir' / 'dir-to-top').write_text("dir-to-top\n")
(mod / 'top-to-escape').write_text("top-to-escape\n")
(trap_outside / 'outside-source').write_text("outside-source\n")
(mod / 'realdir' / 'same-old').write_text("same-old\n")
(mod / 'top-old').write_text("top-old\n")
os.symlink('../trap', mod / 'escape_link')

proc = subprocess.run([str(TOOLDIR / 't_rename_secure'), str(mod)])
if proc.returncode == 77:
    test_skipped("t_rename_secure skipped")
if proc.returncode != 0:
    test_fail("t_rename_secure reported failures (see stderr above)")

assert_not_exists(trap_outside / 'new-outside',
                  f"rename escaped the module and created {trap_outside}/new-outside")
assert_exists(trap_outside / 'outside-source',
              f"rename escaped the module and moved {trap_outside}/outside-source")

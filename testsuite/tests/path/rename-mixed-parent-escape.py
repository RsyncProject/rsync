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
mod.mkdir(parents=True)
trap_outside.mkdir(parents=True)

os.symlink('../trap', mod / 'escape_link')

(mod / 'poc-top-to-escape').write_text("poc-top-to-escape\n")
(trap_outside / 'poc-outside-source').write_text("poc-outside-source\n")
(mod / 'fixed-top-to-escape').write_text("fixed-top-to-escape\n")
(trap_outside / 'fixed-outside-source').write_text("fixed-outside-source\n")

proc = subprocess.run([str(TOOLDIR / 't_rename_secure'), '--poc', str(mod)])
if proc.returncode == 77:
    test_skipped("t_rename_secure --poc skipped")
if proc.returncode != 0:
    test_fail("t_rename_secure --poc reported failures (see stderr above)")

assert_exists(trap_outside / 'vuln-created',
              "PoC did not create outside file via vulnerable destination-parent rename")
assert_exists(mod / 'vuln-stolen',
              "PoC did not move outside file into module via vulnerable source-parent rename")

assert_not_exists(trap_outside / 'fixed-created',
                  "fixed do_rename_at created outside file")
assert_exists(mod / 'fixed-top-to-escape',
              "fixed do_rename_at consumed protected module source")
assert_exists(trap_outside / 'fixed-outside-source',
              "fixed do_rename_at consumed protected outside source")
assert_not_exists(mod / 'fixed-stolen',
                  "fixed do_rename_at moved outside file into module")

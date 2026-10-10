#!/usr/bin/env python3
# Python rewrite of testsuite/rename-mixed-parent-escape-poc.test.
#
# PoC for the 3.4.3 do_rename_at() mixed-parent fallback. The helper
# first runs an emulation of the vulnerable fallback to show the actual
# module escape, then runs the live do_rename_at() against the same
# shape to prove the fixed wrapper rejects it.

import os
import subprocess

from rsyncfns import (
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

# The PoC's emulation of the vulnerable fallback must have escaped the module.
assert_exists(trap_outside / 'vuln-created',
              "PoC did not create outside file via vulnerable destination-parent rename")
assert_exists(mod / 'vuln-stolen',
              "PoC did not move outside file into module via vulnerable source-parent rename")

# The live (fixed) do_rename_at() must have refused every escape.
assert_not_exists(trap_outside / 'fixed-created',
                  "fixed do_rename_at created outside file")
assert_exists(mod / 'fixed-top-to-escape',
              "fixed do_rename_at consumed protected module source")
assert_exists(trap_outside / 'fixed-outside-source',
              "fixed do_rename_at consumed protected outside source")
assert_not_exists(mod / 'fixed-stolen',
                  "fixed do_rename_at moved outside file into module")

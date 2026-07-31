#!/usr/bin/env python3
# Python rewrite of testsuite/chmod-symlink-race.test.
#
# Regression test for the symlink-TOCTOU class of bug applied to chmod()
# on the receiver side. The CVE-2026-29518 fix used
# secure_relative_open() for the basis-file open, but every other
# path-based syscall the receiver runs on sender-controllable paths is
# vulnerable to the same primitive: a local attacker swaps a symlink
# into one of the parent directory components between the receiver's
# check and its act, and the syscall escapes the module.
#
# The helper t_chmod_secure exercises the new do_chmod_at() wrapper
# across four scenarios; see the shell version for the full enumeration.
# After the helper runs we sanity-check the outside sentinel's mode
# from Python too, in case the helper's internal stat() ever drifts.

import os
import subprocess

from rsyncfns import SCRATCHDIR, TOOLDIR, rmtree, test_fail


mod = SCRATCHDIR / 'module'
trap_outside = SCRATCHDIR / 'trap'
rmtree(mod)
rmtree(trap_outside)
mod.mkdir(parents=True)
(mod / 'realdir').mkdir(parents=True)
trap_outside.mkdir(parents=True)

# File-system objects the helper expects.
(mod / 'realdir' / 'sentinel').write_text("bystander\n")
os.chmod(mod / 'realdir' / 'sentinel', 0o600)
(trap_outside / 'sentinel').write_text("target\n")
os.chmod(trap_outside / 'sentinel', 0o600)
os.symlink('realdir', mod / 'inside_link')
os.symlink('../trap', mod / 'escape_link')
(mod / 'topfile').write_text("top\n")
os.chmod(mod / 'topfile', 0o600)

proc = subprocess.run([str(TOOLDIR / 't_chmod_secure'), str(mod)])
if proc.returncode != 0:
    test_fail("t_chmod_secure reported failures (see stderr above)")

# Second-look sanity check from Python.
sentinel_mode = (trap_outside / 'sentinel').stat().st_mode & 0o777
if sentinel_mode != 0o600:
    test_fail(
        f"outside sentinel mode changed from 600 to {oct(sentinel_mode)[2:]} "
        "-- chmod escaped the module"
    )

#!/usr/bin/env python3

import subprocess

from harness.rsync import SCRATCHDIR, TOOLDIR, rmtree, test_fail

testdir = SCRATCHDIR / 'relpath-test'
rmtree(testdir)
testdir.mkdir(parents=True)

proc = subprocess.run([str(TOOLDIR / 't_secure_relpath'), str(testdir)])
if proc.returncode != 0:
    test_fail(
        "t_secure_relpath rejected one or more inputs incorrectly "
        "(see stderr above for the specific case)"
    )

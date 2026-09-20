#!/usr/bin/env python3
# Python rewrite of testsuite/secure-relpath-validation.test.
#
# Regression test for codex audit Finding 5: secure_relative_open()'s
# front-door input check rejects "../foo" and "foo/../bar" but missed
# bare "..", "subdir/..", and other variants whose "/"-split components
# contain a literal "..". RESOLVE_BENEATH equivalents catch these in
# the kernel, but the per-component O_NOFOLLOW fallback (on NetBSD,
# OpenBSD, Solaris, Cygwin, pre-5.6 Linux) does not -- so the
# validation must happen at the front door.
#
# The t_secure_relpath helper runs each suspect input through
# secure_relative_open() and confirms it gets back -1/EINVAL (the
# marker that the front-door check kicked in, not the kernel).

import subprocess

from rsyncfns import SCRATCHDIR, TOOLDIR, rmtree, test_fail


testdir = SCRATCHDIR / 'relpath-test'
rmtree(testdir)
testdir.mkdir(parents=True)

proc = subprocess.run([str(TOOLDIR / 't_secure_relpath'), str(testdir)])
if proc.returncode != 0:
    test_fail(
        "t_secure_relpath rejected one or more inputs incorrectly "
        "(see stderr above for the specific case)"
    )

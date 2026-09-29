#!/usr/bin/env python3
"""The C test helpers that are pure path/string arithmetic.

CMake builds these on Windows but nothing was driving them, which left
t_secure_relpath -- a security-relevant path validator -- compiled and
untested.  Both run unmodified here: neither touches anything Unix-specific.

t_chmod_secure is deliberately not driven.  Its whole subject is the
symlink-TOCTOU primitive behind CVE-2026-29518: every case either builds a
parent symlink that escapes the tree or checks that a legitimate one still
resolves.  A build without symlink support cannot set any of that up, so
running it would report failures that say nothing about this port.

Copyright (C) 2026 Max Vilimpoc, rsync CMake/Windows port.
Distributed under the same GPL-3.0-or-later terms as the rest of rsync.
"""

from wintest import SCRATCH, check, helper, ok

# --- t_secure_relpath ------------------------------------------------------
# secure_relative_open()'s front-door check must reject every spelling of a
# path that climbs out of the receiver's confinement, with -1/EINVAL.  On
# Windows there is no AT_FDCWD, so syscall.c takes the portable fallback --
# which is precisely the path that has no kernel-side RESOLVE_BENEATH to
# catch what the front door misses.  So this matters more here, not less.
relpath_dir = SCRATCH / 'relpath'
relpath_dir.mkdir(parents=True, exist_ok=True)

# rsync 3.5.0 added a check_beneath_dotdot() section to this helper.  It opens
# "." as a directory fd and walks through a symlink, and Windows has neither:
# the CRT's open() refuses a directory (the anchor open fails with EACCES) and
# this port creates no symlinks.  It is the same limitation that makes
# CHDIR_VIA_DIRFD 0 -- and without AT_FDCWD the resolver it exercises degrades
# to a bare open() that checks nothing anyway, so there is no behaviour here to
# have an opinion about.  The front-door cases above it are the part that
# matters on Windows, so those are asserted one by one and the exit code is
# not, rather than losing the whole helper to a single skip.
proc = helper('t_secure_relpath', relpath_dir, expect=None)
report = proc.stdout + proc.stderr   # it writes its case log to stderr

FRONT_DOOR = [
    'relpath=..', 'relpath=../foo', 'relpath=subdir/..',
    'relpath=subdir/../subdir', 'relpath=foo/../bar', 'relpath=/foo',
    'relpath=/', 'basedir=..', 'basedir=../subdir', 'basedir=subdir/..',
    'basedir=foo/../bar',
]
for case in FRONT_DOOR:
    # The helper pads each label, so match within the line, not the whole box.
    check(any(l.startswith('OK') and case in l for l in report.splitlines()),
          f"t_secure_relpath did not reject [{case}]:\n{report}")

# Whatever did fail must be the dirfd/symlink section and nothing else -- a new
# FAIL outside it is a real regression and must not hide behind this.
outside = [l for l in report.splitlines()
           if l.startswith('FAIL') and '[beneath ' not in l]
check(not outside,
      't_secure_relpath failed outside the dirfd-dependent section:\n'
      + '\n'.join(outside))

# --- t_clean_fname (new in 3.5.0) ------------------------------------------
# KI-50: clean_fname(CFN_COLLAPSE_DOT_DOT_DIRS) must collapse "..".  Pure string
# arithmetic on util1.o, so the Windows build exercises the same code upstream's
# clean-fname-collapse test does.
proc = helper('t_clean_fname')
check('FAIL' not in proc.stdout + proc.stderr,
      "t_clean_fname reported a failure:\n" + proc.stdout + proc.stderr)

# --- t_iwildmatch (new in 3.5.0) -------------------------------------------
# iwildmatch() must fold case on the pattern as well as the text.  Matches
# upstream's iwildmatch-fold test; matters here because Windows file names are
# routinely compared case-insensitively.
proc = helper('t_iwildmatch')
check('FAIL' not in proc.stdout + proc.stderr,
      "t_iwildmatch reported a failure:\n" + proc.stdout + proc.stderr)

# --- t_hashtable_overflow (new in 3.5.0) -----------------------------------
# hashtable_create(2^28) overflows int in the unfixed code and under-allocates;
# the fix rejects it and exits RERR_MALLOC.  So a *non-zero* exit is the pass
# here -- upstream's hashtable-overflow test asserts the same code.  Worth
# running on the 32-bit build in particular, where size_t is 32 bits.
RERR_MALLOC = 22   # errcode.h
proc = helper('t_hashtable_overflow', expect=RERR_MALLOC)
ok('t_hashtable_overflow refused the overflowing size (RERR_MALLOC)')

# --- trimslash -------------------------------------------------------------
# The same inputs and expected output as testsuite/trimslash_test.py.
TRIM_INPUTS = [
    "/usr/local/bin",
    "/usr/local/bin/",
    "/usr/local/bin///",
    "//a//",
    "////",
    "/Users/Weird Macintosh Name/// Ooh, translucent plastic/",
]
TRIM_EXPECTED = """\
/usr/local/bin
/usr/local/bin
/usr/local/bin
//a
/
/Users/Weird Macintosh Name/// Ooh, translucent plastic
"""

proc = helper('trimslash', *TRIM_INPUTS)
got = proc.stdout.replace('\r\n', '\n')
check(got == TRIM_EXPECTED,
      "trimslash output did not match:\n"
      f"--- expected ---\n{TRIM_EXPECTED}--- got ---\n{got}")

ok('t_secure_relpath and trimslash behave as upstream expects')

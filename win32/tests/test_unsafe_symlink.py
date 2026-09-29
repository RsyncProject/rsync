#!/usr/bin/env python3
"""rsync's unsafe_symlink() unit test.

t_unsafe wraps unsafe_symlink(), which decides whether a symlink's target
escapes the transfer root.  It is pure path arithmetic, so it behaves the
same on Windows even though the port needs a privilege to *create* a
symlink.  Cases are taken from testsuite/unsafe-byname_test.py, plus a
drive-letter case that exercises this port's IS_ABS_PATH().

Copyright (C) 2026 Max Vilimpoc, rsync CMake/Windows port.
Distributed under the same GPL-3.0-or-later terms as the rest of rsync.
"""

from wintest import SCRATCH, check, helper, ok

# (link target, current dir, expected verdict)
CASES = [
    ('file', 'from', 'safe'),
    ('dir/file', 'from', 'safe'),
    ('dir/./file', 'from', 'safe'),
    ('dir/.', 'from', 'safe'),
    ('dir/', 'from', 'safe'),

    ('/etc/passwd', 'from', 'unsafe'),
    ('//../etc/passwd', 'from', 'unsafe'),
    ('//./etc/passwd', 'from', 'unsafe'),

    ('./foo', 'from', 'safe'),
    ('../foo', 'from', 'unsafe'),
    ('./../foo', 'from', 'unsafe'),
    ('.//../foo', 'from', 'unsafe'),
    ('./../foo', 'from/..', 'unsafe'),
    ('../dest', 'from/dir', 'safe'),
    ('../../dest', 'from//dir', 'unsafe'),
    ('..//../dest', 'from/dir', 'unsafe'),

    ('..', 'from/file', 'safe'),
    ('../..', 'from/file', 'unsafe'),
    ('..//..', 'from//file', 'unsafe'),
    ('dir/..', 'from', 'unsafe'),
    ('dir/../..', 'from', 'unsafe'),
    ('dir/..//..', 'from', 'unsafe'),

    ('', 'from', 'unsafe'),

    ('../../unsafe/unsafefile', 'from/safe', 'unsafe'),
    ('..//../unsafe/unsafefile', 'from/safe', 'unsafe'),
    ('../files/file1', 'from/safe', 'safe'),

    ('../../unsafe/unsafefile', 'safe', 'unsafe'),
    ('../files/file1', 'safe', 'unsafe'),
]

# An absolute curdir makes any relative target safe, because it can never
# climb out of the root.  Spelling it with a drive letter is what proves
# IS_ABS_PATH() recognises "C:/..." and not just a leading slash.
abs_dir = str(SCRATCH / 'from' / 'safe').replace('\\', '/')
CASES += [
    ('../../unsafe/unsafefile', abs_dir, 'safe'),
    ('../files/file1', abs_dir, 'safe'),
]

failures = []
for target, curdir, expected in CASES:
    got = helper('t_unsafe', target, curdir).stdout.strip()
    if got != expected:
        failures.append(
            f"t_unsafe {target!r} {curdir!r} -> {got!r}, expected {expected!r}")

check(not failures, '\n'.join(failures))
ok(f'{len(CASES)} unsafe_symlink cases correct')

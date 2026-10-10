#!/usr/bin/env python3

import os
import subprocess

from harness.rsync import TOOLDIR, test_fail

t_unsafe = str(TOOLDIR / 't_unsafe')

pwd = os.getcwd()

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

    ('../../unsafe/unsafefile', f'{pwd}/from/safe', 'safe'),
    ('../files/file1', f'{pwd}/from/safe', 'safe'),
]

failures = []
for target, curdir, expected in CASES:
    proc = subprocess.run(
        [t_unsafe, target, curdir],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        test_fail(f"Failed to check {target!r} {curdir!r}: exit {proc.returncode}")
    got = proc.stdout.strip()
    if got != expected:
        failures.append(
            f"t_unsafe {target!r} {curdir!r} returned {got!r}, expected {expected!r}"
        )

if failures:
    test_fail("\n".join(failures))

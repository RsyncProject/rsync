#!/usr/bin/env python3

import subprocess

from harness.rsync import (
    SCRATCHDIR, rmtree, rsync_argv, start_test_daemon, test_fail, write_daemon_conf,
)

DAEMON_PORT = 13010

CASES = [
    ('bare',    '/excluded',     True),
    ('dir',     '/excluded/',    False),
    ('triple',  '/excluded/***', False),
]

base = SCRATCHDIR / 'daemon-exclude-namebased'
rmtree(base)
base.mkdir()
src = base / 'hosts'
src.write_text("127.0.0.1 localhost\n")

modules = []
for name, pattern, _ in CASES:
    mod = base / name
    (mod / 'excluded').mkdir(parents=True)
    modules.append((name, {'path': str(mod), 'read only': 'no', 'exclude': pattern}))

url = start_test_daemon(write_daemon_conf(modules), DAEMON_PORT)

for name, pattern, expect_landed in CASES:
    subprocess.run(
        rsync_argv('-a', str(src), f'{url}{name}/excluded/x2.tx'),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    landed = (base / name / 'excluded' / 'x2.tx').exists()
    if landed != expect_landed:
        verb = "did not land" if expect_landed else "landed"
        test_fail(
            f"exclude={pattern!r}: a file push to excluded/x2.tx {verb} "
            f"(landed={landed}, expected {expect_landed}).  A bare anchored name "
            "protects only an entry of that exact name; a subtree needs a "
            "trailing-slash or /*** pattern (rsyncd.conf(5)).")

print("daemon exclude is name-based: a bare-name pattern does not protect the "
      "subtree; a /dir/ or /dir/*** pattern does (3.2.7-equivalent)")

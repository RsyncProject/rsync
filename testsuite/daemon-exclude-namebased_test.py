#!/usr/bin/env python3
# The daemon exclude/filter matches the LOGICAL module-relative NAME, anchored at
# the module root -- it is a visibility/tamper filter, not a physical-path
# boundary.  rsyncd.conf(5) (filter parameter) says a subtree must be excluded
# with a trailing-slash or "/***" pattern; a bare anchored name protects only an
# entry literally of that name, not paths under it.  This test makes that
# documented, name-based behaviour executable (it matches stock rsync 3.2.7).
#
# A direct push of a FILE to <excluded>/x2.tx:
#   exclude = /excluded      (bare name)   -> LANDS  (subtree not protected)
#   exclude = /excluded/     (dir form)    -> refused
#   exclude = /excluded/***  (subtree)     -> refused
# One daemon serves three modules (one per pattern) so a single rsyncd handles
# all cases -- avoids multi-daemon port/pid churn on some platforms.  Unprivileged.

import subprocess

from rsyncfns import (
    SCRATCHDIR, rmtree, rsync_argv, start_test_daemon, test_fail, write_daemon_conf,
)

DAEMON_PORT = 13010

# (module name, exclude pattern, expect the pushed file to land)
CASES = [
    ('bare',    '/excluded',     True),   # bare name: does NOT protect the subtree
    ('dir',     '/excluded/',    False),  # dir form: protects the subtree
    ('triple',  '/excluded/***', False),  # documented subtree form: protects
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

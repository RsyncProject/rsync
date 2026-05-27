#!/usr/bin/env python3
"""Daemon coverage: pre-xfer exec and post-xfer exec.

A module's pre-xfer/post-xfer exec hooks must run with the documented
environment (RSYNC_MODULE_NAME, RSYNC_EXIT_STATUS, ...), and a non-zero
pre-xfer exec must abort the transfer.
"""

import subprocess
import time

from rsyncfns import (
    FROMDIR, SCRATCHDIR,
    make_tree, makepath, rmtree, rsync_argv, start_test_daemon, test_fail,
    write_daemon_conf,
)


def wait_for(path, want, secs=5):
    """Poll for a marker file to contain `want`. post-xfer exec runs on the
    daemon side after the client disconnects (a real race under --use-tcp),
    so we must wait for it rather than check immediately."""
    deadline = time.monotonic() + secs
    while time.monotonic() < deadline:
        if path.is_file() and path.read_text().strip() == want:
            return True
        time.sleep(0.05)
    return False

DAEMON_PORT = 12889

src = FROMDIR
rmtree(src)
make_tree(src, depth=3)

markers = SCRATCHDIR / 'markers'
rmtree(markers)
makepath(markers)
hookdir = SCRATCHDIR / 'hookdest'
faildir = SCRATCHDIR / 'faildest'
makepath(hookdir, faildir)


def script(name, body):
    p = SCRATCHDIR / name
    p.write_text('#!/bin/sh\n' + body)
    p.chmod(0o755)
    return p


pre = script('pre.sh', f'echo "$RSYNC_MODULE_NAME" > {markers}/pre.out\nexit 0\n')
post = script('post.sh', f'echo "$RSYNC_EXIT_STATUS" > {markers}/post.out\n'
                         'exit 0\n')
prefail = script('prefail.sh', 'exit 1\n')

conf = write_daemon_conf([
    ('hook', {'path': hookdir, 'read only': 'no',
              'pre-xfer exec': pre, 'post-xfer exec': post}),
    ('failhook', {'path': faildir, 'read only': 'no',
                  'pre-xfer exec': prefail}),
])
url = start_test_daemon(conf, DAEMON_PORT)

# --- pre/post hooks run with the documented environment ---------------------
proc = subprocess.run(rsync_argv('-a', f'{src}/', f'{url}hook/'),
                      stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                      text=True)
if proc.returncode not in (0, 23):
    test_fail(f"transfer through exec-hook module failed: {proc.stderr}")
if not wait_for(markers / 'pre.out', 'hook'):
    test_fail("pre-xfer exec did not run with RSYNC_MODULE_NAME=hook")
if not wait_for(markers / 'post.out', '0'):
    test_fail("post-xfer exec did not run with RSYNC_EXIT_STATUS=0")

# --- a failing pre-xfer exec aborts the transfer ----------------------------
proc = subprocess.run(rsync_argv('-a', f'{src}/', f'{url}failhook/'),
                      stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                      text=True)
if proc.returncode == 0:
    test_fail("a failing pre-xfer exec did not abort the transfer")
if list(faildir.iterdir()):
    test_fail("transfer wrote files despite a failing pre-xfer exec")

print("daemon-exec: pre-xfer/post-xfer exec env + abort-on-failure verified")

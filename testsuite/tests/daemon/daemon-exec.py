#!/usr/bin/env python3

import shlex
import subprocess
import time

from harness.rsync import (
    FROMDIR, SCRATCHDIR,
    make_tree, makepath, rmtree, rsync_argv, start_test_daemon, test_fail,
    write_daemon_conf, write_text_file,
)
from harness import metadata

metadata(features={'daemon', 'exec-hooks'}, transports={'pipe', 'tcp'}, min_peer='2.6.0', mutates={'filesystem', 'process', 'socket'}, tags={'daemon', 'process', 'version-mix'})

def wait_for(path, want, secs=5):
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

pre = write_text_file(
    SCRATCHDIR / 'pre.sh',
    f'#!/bin/sh\necho "$RSYNC_MODULE_NAME" > {shlex.quote(str(markers / "pre.out"))}\nexit 0\n',
    0o755,
)
post = write_text_file(
    SCRATCHDIR / 'post.sh',
    f'#!/bin/sh\necho "$RSYNC_EXIT_STATUS" > {shlex.quote(str(markers / "post.out"))}\nexit 0\n',
    0o755,
)
prefail = write_text_file(SCRATCHDIR / 'prefail.sh', '#!/bin/sh\nexit 1\n', 0o755)

conf = write_daemon_conf([
    ('hook', {'path': hookdir, 'read only': 'no',
              'pre-xfer exec': shlex.quote(str(pre)),
               'post-xfer exec': shlex.quote(str(post))}),
    ('failhook', {'path': faildir, 'read only': 'no',
                  'pre-xfer exec': shlex.quote(str(prefail))}),
])
url = start_test_daemon(conf, DAEMON_PORT)

proc = subprocess.run(rsync_argv('-a', f'{src}/', f'{url}hook/'),
                      stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                      text=True)
if proc.returncode not in (0, 23):
    test_fail(f"transfer through exec-hook module failed: {proc.stderr}")
if not wait_for(markers / 'pre.out', 'hook'):
    test_fail("pre-xfer exec did not run with RSYNC_MODULE_NAME=hook")
if not wait_for(markers / 'post.out', '0'):
    test_fail("post-xfer exec did not run with RSYNC_EXIT_STATUS=0")

proc = subprocess.run(rsync_argv('-a', f'{src}/', f'{url}failhook/'),
                      stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                      text=True)
if proc.returncode == 0:
    test_fail("a failing pre-xfer exec did not abort the transfer")
if list(faildir.iterdir()):
    test_fail("transfer wrote files despite a failing pre-xfer exec")

print("daemon-exec: pre-xfer/post-xfer exec env + abort-on-failure verified")

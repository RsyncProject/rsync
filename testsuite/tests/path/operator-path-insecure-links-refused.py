#!/usr/bin/env python3

import subprocess

from harness.rsync import (
    SCRATCHDIR, rmtree, rsync_argv, start_test_daemon, test_fail,
    write_daemon_conf,
)

DAEMON_PORT = 12902

base = SCRATCHDIR / 'insecure-refused'
rmtree(base)
base.mkdir()
mod = base / 'mod'
mod.mkdir()
(mod / 'f').write_text("data\n")

conf = write_daemon_conf([('mod', {'path': mod, 'read only': 'yes'})])
url = start_test_daemon(conf, DAEMON_PORT)

ok = subprocess.run(rsync_argv(f'{url}mod/'),
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
if ok.returncode != 0:
    test_fail(f"control listing failed (daemon broken?): {ok.stderr}")

proc = subprocess.run(rsync_argv('-M--insecure-links', f'{url}mod/'),
                      stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
out = (proc.stdout or '') + (proc.stderr or '')
if proc.returncode == 0:
    test_fail("the daemon accepted a client-sent --insecure-links instead of "
              f"dropping the connection: {out!r}")
if 'insecure-links' not in out and 'not allowed' not in out:
    test_fail("the connection ended but without the expected refusal message "
              f"(@ERROR: --insecure-links is not allowed from a client): {out!r}")
print("daemon drops the connection on a client-sent --insecure-links")

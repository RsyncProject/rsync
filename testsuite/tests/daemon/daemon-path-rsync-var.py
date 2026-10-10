#!/usr/bin/env python3

import subprocess

from harness.daemon_config import write_daemon_conf
from harness.rsync import SCRATCHDIR, rmtree, rsync_argv, start_test_daemon, test_fail

DAEMON_PORT = 12906

base = SCRATCHDIR / 'daemon-path-var'
rmtree(base)
root = base / 'root'
served = root / 'served'
served.mkdir(parents=True)
(served / 'hello.txt').write_text('EXPANDED\n')

conf = write_daemon_conf([
    ('served', {'path': f'{root}/%RSYNC_MODULE_NAME%',
                'use chroot': 'no', 'read only': 'yes'}),
])
url = start_test_daemon(conf, DAEMON_PORT).rstrip('/')

dest = base / 'dest'
dest.mkdir(parents=True)
proc = subprocess.run(
    rsync_argv('-a', f'{url}/served/hello.txt', str(dest) + '/'),
    capture_output=True, text=True)

got = dest / 'hello.txt'
if proc.returncode != 0 or not got.is_file():
    test_fail("%RSYNC_MODULE_NAME% in `path` did not expand to the raw value "
              "(the daemon could not chdir -- the path was shell-quoted); "
              f"rc={proc.returncode}\nstderr: {proc.stderr.strip()}")
if got.read_text() != 'EXPANDED\n':
    test_fail(f"unexpected content from the expanded-path module: {got.read_text()!r}")

print("daemon-path-rsync-var: %RSYNC_*% in a string param expands unquoted")

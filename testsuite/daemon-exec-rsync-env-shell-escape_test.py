#!/usr/bin/env python3
import os
import subprocess

from rsyncfns import SCRATCHDIR, rmtree, rsync_argv, start_test_daemon, test_fail, write_daemon_conf

base = SCRATCHDIR / 'exec-env-escape'
rmtree(base)
src = base / 'src'
src.mkdir(parents=True)
(src / 'f').write_text('payload\n')
sentinel = base / 'pwned'

os.environ['RSYNC_REQUEST'] = f"ok;touch {sentinel};#"
conf = write_daemon_conf([
    ('execmod', {'path': str(src), 'read only': 'yes',
                 'pre-xfer exec': 'sh -c "printf %RSYNC_REQUEST% >/dev/null"'}),
])
url = start_test_daemon(conf, 12936)
proc = subprocess.run(
    rsync_argv('-r', f'{url}execmod/'),
    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
os.environ.pop('RSYNC_REQUEST', None)

if sentinel.exists():
    test_fail("daemon exec-hook %RSYNC_REQUEST% expansion executed injected shell syntax")
if proc.returncode != 0:
    test_fail(f"daemon exec-hook transfer failed unexpectedly:\n{proc.stderr}")

print("daemon-exec-rsync-env-shell-escape: %RSYNC_*% expansion is shell-escaped")

#!/usr/bin/env python3

import os
import pwd
import subprocess
import sys
import time

from harness import metadata
from harness.daemon import start_test_daemon
from harness.daemon_config import write_daemon_conf
from harness.environment import FROMDIR, SCRATCHDIR, SRCDIR
from harness.filesystem import make_tree, makepath, rmtree, write_text_file
from harness.results import test_fail, test_skipped
from harness.rsync import rsync_argv

metadata(features={'ownership'}, transports={'pipe', 'tcp'})

rmtree(FROMDIR)
make_tree(FROMDIR, depth=2)
destination = SCRATCHDIR / 'destination'
log = SCRATCHDIR / 'name-converter.log'
makepath(destination)
converter = write_text_file(
    SCRATCHDIR / 'name-converter',
    f'#!{sys.executable}\nimport subprocess, sys\n'
    f'child = subprocess.Popen([sys.executable, {str(SRCDIR / "support/nameconvert")!r}], '
    'stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)\n'
    f'with open({str(log)!r}, "a", buffering=1) as output:\n'
    '    for line in sys.stdin:\n'
    '        output.write(line)\n'
    '        child.stdin.write(line); child.stdin.flush()\n'
    '        reply = child.stdout.readline()\n'
    '        if not reply: break\n'
    '        print(reply, end="", flush=True)\n'
    'child.stdin.close()\nraise SystemExit(child.wait())\n',
    0o755,
)
account = next((entry for entry in pwd.getpwall()
                if entry.pw_uid != 0 and entry.pw_gid != 0), None)
if account is None:
    test_skipped('no non-root account is available', capability='ownership')
for path in FROMDIR.rglob('*'):
    os.chown(path, account.pw_uid, account.pw_gid)
config = write_daemon_conf([
    ('convert', {'path': str(destination), 'read only': 'no', 'use chroot': 'no',
                 'name converter': str(converter), 'numeric ids': 'no'}),
])
url = start_test_daemon(config, 12892)
result = subprocess.run(rsync_argv('-rog', f'{FROMDIR}/', f'{url}convert/'),
                        capture_output=True, text=True)
if result.returncode:
    test_fail(f'name-converter transfer failed ({result.returncode}):\n{result.stderr}')
deadline = time.monotonic() + 5
while time.monotonic() < deadline and not (log.is_file() and log.stat().st_size):
    time.sleep(0.05)
requests = log.read_text().splitlines() if log.is_file() else []
if not any(line.startswith('usr ') for line in requests):
    test_fail(f'name converter received no user request: {requests!r}')
if not any(line.startswith('grp ') for line in requests):
    test_fail(f'name converter received no group request: {requests!r}')
received = next(path for path in destination.rglob('*') if path.is_file())
if received.stat().st_uid != account.pw_uid:
    test_fail(f'name converter mapped uid {account.pw_uid} to {received.stat().st_uid}')

print(f'daemon-name-converter: handled {len(requests)} requests')

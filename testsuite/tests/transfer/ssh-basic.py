#!/usr/bin/env python3

import os
import re
import shlex
import shutil
import subprocess

from harness import rsync
from harness.rsync import (
    FROMDIR,
    RSYNC_PEER,
    TODIR,
    checkit,
    hands_setup,
    runtest,
    test_skipped,
    rsync_path_arg,
    rsh_cmd,
    split_rsync_cmd,
)
from harness import metadata

metadata(features={'remote-shell'}, transports={'pipe', 'tcp'}, min_peer='2.6.0', mutates={'environment', 'filesystem', 'process'}, tags={'compatibility', 'transfer', 'version-mix'})

SSH = rsh_cmd()

if os.environ.get('rsync_enable_ssh_tests') == 'yes':
    real_ssh = shutil.which('ssh')
    if real_ssh:
        SSH = real_ssh

probe = subprocess.run(
    [*shlex.split(SSH), '-oBatchMode yes', 'localhost', 'echo', 'yes'],
    capture_output=True, text=True,
)
if probe.stdout.strip() != 'yes':
    test_skipped(
        "Skipping SSH tests because ssh connection to localhost not authorised"
    )

print(f"Using remote shell: {SSH}")

compare_link_times = '-l' in shlex.split(rsync.TLS_ARGS)
peer_version = subprocess.run(
    [*split_rsync_cmd(RSYNC_PEER), '--version'],
    capture_output=True, text=True,
)
match = re.search(r'rsync\s+version\s+(\d+)\.(\d+)\.(\d+)',
                  peer_version.stdout)
if peer_version.returncode == 0 and match and int(match.group(1)) < 3:
    rsync.TLS_ARGS = ' '.join(
        arg for arg in shlex.split(rsync.TLS_ARGS) if arg != '-l'
    )

hands_setup()
if compare_link_times:
    os.utime(FROMDIR / 'nolf-symlink', (1_000_000_000, 1_000_000_000),
             follow_symlinks=False)

def _basic():
    checkit(['-avH', '-e', SSH, f'--rsync-path={rsync_path_arg()}',
             f'{FROMDIR}/', f'localhost:{TODIR}'], FROMDIR, TODIR)

def _delete_after_rename():
    shutil.move(str(TODIR / 'text'), str(TODIR / 'ThisShouldGo'))
    checkit(['--delete', '-avH', '-e', SSH, f'--rsync-path={rsync_path_arg()}',
             f'{FROMDIR}/', f'localhost:{TODIR}'], FROMDIR, TODIR)

runtest("ssh: basic test", _basic)
runtest("ssh: renamed file", _delete_after_rename)

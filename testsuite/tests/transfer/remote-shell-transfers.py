#!/usr/bin/env python3

import os
import re
import shlex
import shutil
import subprocess

from harness import environment
from fixtures.trees import hands_setup
from harness.comparison import checkit
from harness.environment import FROMDIR, TODIR
from harness.process import split_rsync_cmd
from harness.results import test_skipped
from harness.rsync import rsh_cmd, rsync_path_arg, runtest
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

listing_args = shlex.split(environment.TLS_ARGS)
compare_link_times = any(arg in ('-l', '--link-times') for arg in listing_args)
if compare_link_times:
    banner = subprocess.check_output(split_rsync_cmd(environment.RSYNC_PEER) + ['--version'], text=True, timeout=10)
    compare_link_times = re.search(r'(?<!no )\bsymtimes\b', ' '.join(banner.split())) is not None
    if not compare_link_times:
        environment.TLS_ARGS = shlex.join(arg for arg in listing_args if arg not in ('-l', '--link-times'))
hands_setup()
if compare_link_times:
    os.utime(FROMDIR / 'nolf-symlink', (1_000_000_000, 1_000_000_000), follow_symlinks=False)

def _basic():
    checkit(['-avH', '-e', SSH, f'--rsync-path={rsync_path_arg()}',
             f'{FROMDIR}/', f'localhost:{TODIR}'], FROMDIR, TODIR)

def _delete_after_rename():
    shutil.move(str(TODIR / 'text'), str(TODIR / 'ThisShouldGo'))
    checkit(['--delete', '-avH', '-e', SSH, f'--rsync-path={rsync_path_arg()}',
             f'{FROMDIR}/', f'localhost:{TODIR}'], FROMDIR, TODIR)

runtest("ssh: basic test", _basic)
runtest("ssh: renamed file", _delete_after_rename)

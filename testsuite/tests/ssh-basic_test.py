#!/usr/bin/env python3
# Python rewrite of testsuite/ssh-basic.test.
#
# Basic two-step "remote shell" transfer via lsh.sh (or real ssh if
# rsync_enable_ssh_tests=yes is set in shconfig). Confirms that an -e
# RSH transfer reproduces the source tree on the destination, and that
# a follow-up --delete pass cleans up after a destination-side rename.

import os
import re
import shlex
import shutil
import subprocess

import rsyncfns
from rsyncfns import (
    FROMDIR, RSYNC_PEER, SRCDIR, TODIR,
    checkit, hands_setup, runtest, test_skipped, rsync_path_arg, rsh_cmd, split_rsync_cmd,
)
from harness import metadata

metadata(features={'remote-shell'}, protocols={27, 28, 29, 30, 31, 32, 33}, transports={'pipe', 'tcp'}, min_peer='2.6.0', mutates={'environment', 'filesystem', 'process'}, tags={'compatibility', 'transfer', 'version-mix'})

SSH = rsh_cmd()

# Allow opting into real ssh via the shconfig variable, like the shell test.
if os.environ.get('rsync_enable_ssh_tests') == 'yes':
    real_ssh = shutil.which('ssh')
    if real_ssh:
        SSH = real_ssh

# SSH is quoted for rsync's own tokenizer (a build path with '~' or a space
# comes out in quotes), so split it the same way before exec'ing it directly.
probe = subprocess.run(
    [*shlex.split(SSH), '-oBatchMode yes', 'localhost', 'echo', 'yes'],
    capture_output=True, text=True,
)
if probe.stdout.strip() != 'yes':
    test_skipped(
        "Skipping SSH tests because ssh connection to localhost not authorised"
    )

print(f"Using remote shell: {SSH}")

listing_args = shlex.split(rsyncfns.TLS_ARGS)
compare_link_times = any(arg in ('-l', '--link-times') for arg in listing_args)
if compare_link_times:
    banner = subprocess.check_output(split_rsync_cmd(RSYNC_PEER) + ['--version'], text=True, timeout=10)
    compare_link_times = re.search(r'(?<!no )\bsymtimes\b', ' '.join(banner.split())) is not None
    if not compare_link_times:
        rsyncfns.TLS_ARGS = shlex.join(arg for arg in listing_args if arg not in ('-l', '--link-times'))
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

#!/usr/bin/env python3
# Python rewrite of testsuite/ssh-basic.test.
#
# Basic two-step "remote shell" transfer via lsh.sh (or real ssh if
# rsync_enable_ssh_tests=yes is set in shconfig). Confirms that an -e
# RSH transfer reproduces the source tree on the destination, and that
# a follow-up --delete pass cleans up after a destination-side rename.

import os
import shutil
import subprocess

from rsyncfns import (
    FROMDIR, SRCDIR, TODIR,
    checkit, hands_setup, runtest, test_skipped,
)


SSH = str(SRCDIR / 'support' / 'lsh.sh')

# Allow opting into real ssh via the shconfig variable, like the shell test.
if os.environ.get('rsync_enable_ssh_tests') == 'yes':
    real_ssh = shutil.which('ssh')
    if real_ssh:
        SSH = real_ssh

probe = subprocess.run(
    [SSH, '-oBatchMode yes', 'localhost', 'echo', 'yes'],
    capture_output=True, text=True,
)
if probe.stdout.strip() != 'yes':
    test_skipped(
        "Skipping SSH tests because ssh connection to localhost not authorised"
    )

print(f"Using remote shell: {SSH}")

hands_setup()

# RSYNC may be a multi-word command line; pass it through --rsync-path.
from rsyncfns import RSYNC


def _basic():
    checkit(['-avH', '-e', SSH, f'--rsync-path={RSYNC}',
             f'{FROMDIR}/', f'localhost:{TODIR}'], FROMDIR, TODIR)


def _delete_after_rename():
    shutil.move(str(TODIR / 'text'), str(TODIR / 'ThisShouldGo'))
    checkit(['--delete', '-avH', '-e', SSH, f'--rsync-path={RSYNC}',
             f'{FROMDIR}/', f'localhost:{TODIR}'], FROMDIR, TODIR)


runtest("ssh: basic test", _basic)
runtest("ssh: renamed file", _delete_after_rename)

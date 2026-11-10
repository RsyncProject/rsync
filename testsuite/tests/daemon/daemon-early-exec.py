#!/usr/bin/env python3

import subprocess
import sys

from harness import metadata
from harness.daemon import start_test_daemon
from harness.daemon_config import write_daemon_conf
from harness.environment import FROMDIR, SCRATCHDIR
from harness.filesystem import make_tree, makepath, rmtree, write_text_file
from harness.results import test_fail
from harness.rsync import rsync_argv

metadata(transports={'pipe', 'tcp'})

rmtree(FROMDIR)
make_tree(FROMDIR, depth=2)
destination = SCRATCHDIR / 'destination'
marker = SCRATCHDIR / 'early-marker'
makepath(destination)
hook = write_text_file(
    SCRATCHDIR / 'early-hook',
    f'#!{sys.executable}\nimport os, sys\nfrom pathlib import Path\n'
    f'Path({str(marker)!r}).write_bytes(os.fsencode(os.environ["RSYNC_MODULE_NAME"] + "|") '
    '+ sys.stdin.buffer.read())\n',
    0o755,
)
early_input = SCRATCHDIR / 'early-input'
early_input.write_bytes(b'EARLY-PAYLOAD')
config = write_daemon_conf([
    ('early', {'path': str(destination), 'read only': 'no', 'use chroot': 'no',
               'early exec': str(hook)}),
])
url = start_test_daemon(config, 12891)
result = subprocess.run(
    rsync_argv('-r', f'--early-input={early_input}', f'{FROMDIR}/', f'{url}early/'),
    capture_output=True, text=True,
)
if result.returncode:
    test_fail(f'early-exec transfer failed ({result.returncode}):\n{result.stderr}')
if not marker.is_file() or marker.read_bytes() != b'early|EARLY-PAYLOAD':
    test_fail('early-exec received the wrong module name or input')

print('daemon-early-exec: module environment and early input verified')

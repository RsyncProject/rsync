#!/usr/bin/env python3

import subprocess

from harness import metadata
from harness.daemon_config import write_daemon_conf
from harness.comparison import verify_dirs
from harness.daemon import start_test_daemon
from harness.environment import FROMDIR, TODIR
from harness.results import test_fail
from harness.rsync import rsync_argv, run_rsync

metadata(features={'daemon'}, transports={'pipe', 'tcp'}, min_peer='3.2.0', tags={'configuration', 'daemon', 'version-mix'})

FROMDIR.mkdir()
(FROMDIR / 'file').write_text('contents\n')
config = write_daemon_conf([('module', {'path': str(FROMDIR), 'read only': 'yes',
                                      'refuse options': '* !a !v'})])
url = start_test_daemon(config, 12893)
run_rsync('-av', f'{url}module/', f'{TODIR}/')
verify_dirs(FROMDIR, TODIR)
result = subprocess.run(rsync_argv('-avz', f'{url}module/', f'{TODIR}/'),
                        capture_output=True, text=True)
if result.returncode == 0 or 'configured to refuse' not in result.stderr:
    test_fail(f'compression was not refused by the allow list: {result.stderr}')

#!/usr/bin/env python3

from harness import metadata
from harness.daemon_config import write_daemon_conf
from harness.daemon import start_test_daemon
from harness.environment import FROMDIR, TODIR
from harness.filesystem import assert_not_exists, assert_same
from harness.results import test_fail
from harness.rsync import run_rsync

metadata(features={'daemon'}, transports={'pipe', 'tcp'}, min_peer='2.6.0', tags={'daemon', 'version-mix'})

FROMDIR.mkdir()
(FROMDIR / 'a').write_text('excluded\n')
(FROMDIR / 'kept').write_text('contents\n')
TODIR.mkdir()
config = write_daemon_conf([('module', {'path': str(TODIR), 'read only': 'no', 'exclude': '?'})])
url = start_test_daemon(config, 12896)
result = run_rsync('-r', f'{FROMDIR}/', f'{url}module/', check=False)
if result.returncode not in (0, 23):
    test_fail(f'unexpected filter status: {result.returncode}')
if not (TODIR / 'kept').is_file():
    test_fail('the one-character filter excluded the module root')
assert_same(FROMDIR / 'kept', TODIR / 'kept')
assert_not_exists(TODIR / 'a')

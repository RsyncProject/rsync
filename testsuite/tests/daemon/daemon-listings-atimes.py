#!/usr/bin/env python3

from harness import metadata
from harness.daemon_config import write_daemon_conf
from harness.rsync import FROMDIR, run_rsync, start_test_daemon, test_fail, test_skipped

metadata(features={'atimes', 'daemon'}, transports={'pipe', 'tcp'}, min_peer='3.2.0', tags={'daemon', 'version-mix'})

if '"atimes": true' not in run_rsync('-VV', capture_output=True).stdout:
    test_skipped('atimes are unavailable', capability='atimes')
directory = FROMDIR / 'foo'
directory.mkdir(parents=True)
(directory / 'one').write_text('contents\n')
config = write_daemon_conf([('module', {'path': str(FROMDIR), 'read only': 'yes'})])
url = start_test_daemon(config, 12878)
listing = run_rsync('-rU', f'{url}module/f*', capture_output=True).stdout
names = {line.split()[-1] for line in listing.splitlines() if line and line[0] in '-dlcbps'}
if names != {'foo', 'foo/one'}:
    test_fail(f'-U listing returned {sorted(names)}')

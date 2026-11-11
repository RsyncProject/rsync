#!/usr/bin/env python3

from harness import metadata
from harness.daemon_config import write_daemon_conf
from harness.daemon import start_test_daemon
from harness.environment import FROMDIR, SCRATCHDIR, TODIR
from harness.filesystem import assert_same
from harness.results import test_fail
from harness.rsync import run_rsync

metadata(features={'daemon'}, transports={'pipe'},
         min_peer={'primary': '2.6.0', 'secondary': '3.2.2'},
         tags={'configuration', 'daemon', 'version-mix'})

FROMDIR.mkdir()
(FROMDIR / 'logging-marker').write_text('contents\n')
config = write_daemon_conf([('module', {'path': str(TODIR), 'read only': 'no'})],
                           global_options={'transfer logging': 'yes'})
TODIR.mkdir()
url = start_test_daemon(config, 12894)
run_rsync('-r', f'{FROMDIR}/', f'{url}module/')
assert_same(FROMDIR / 'logging-marker', TODIR / 'logging-marker')
if 'logging-marker' not in (SCRATCHDIR / 'rsyncd.log').read_text():
    test_fail('the default transfer log omitted the file')

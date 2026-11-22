#!/usr/bin/env python3

from harness import metadata
from harness.daemon_config import build_rsyncd_conf
from fixtures.trees import hands_setup
from harness.comparison import checkit
from harness.daemon import start_test_daemon
from harness.environment import CHKDIR, FROMDIR, TODIR
from harness.filesystem import rmtree
from harness.rsync import run_rsync

metadata(features={'compression', 'daemon'}, transports={'pipe', 'tcp'}, min_peer='3.1.1', mutates={'filesystem', 'process', 'socket'}, tags={'daemon', 'transfer', 'version-mix'})

hands_setup()
run_rsync('-av', '--exclude=foobar.baz', f'{FROMDIR}/', f'{CHKDIR}/')
url = start_test_daemon(build_rsyncd_conf(), 12879)

checkit(['-avvvvzz', f'{FROMDIR}/', f'{url}test-to/'], CHKDIR, TODIR, allowed_codes=(0, 23))
rmtree(TODIR)
TODIR.mkdir()
checkit(['-avvvvzz', f'{url}test-from/', f'{TODIR}/'], CHKDIR, TODIR, allowed_codes=(0, 23))

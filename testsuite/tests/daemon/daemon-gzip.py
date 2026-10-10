#!/usr/bin/env python3

from harness import metadata
from harness.rsync import (CHKDIR, FROMDIR, TODIR, build_rsyncd_conf, checkit, hands_setup, rmtree,
                      run_rsync, start_test_daemon)

metadata(features={'compression', 'daemon'}, transports={'pipe', 'tcp'}, min_peer='2.6.0', mutates={'filesystem', 'process', 'socket'}, tags={'daemon', 'transfer', 'version-mix'})

hands_setup()
run_rsync('-av', '--exclude=foobar.baz', f'{FROMDIR}/', f'{CHKDIR}/')
url = start_test_daemon(build_rsyncd_conf(), 12879)

checkit(['-avvvvzz', f'{FROMDIR}/', f'{url}test-to/'], CHKDIR, TODIR, allowed_codes=(0, 23))
rmtree(TODIR)
TODIR.mkdir()
checkit(['-avvvvzz', f'{url}test-from/', f'{TODIR}/'], CHKDIR, TODIR, allowed_codes=(0, 23))

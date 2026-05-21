#!/usr/bin/env python3
# Python rewrite of testsuite/daemon-gzip-download.test.
#
# Download a file tree over a compressed connection from an in-process
# rsyncd (via RSYNC_CONNECT_PROG). Exercises (exorcises?) a bug in
# 2.5.3 that mis-handled doubly-compressed transfers.

import os

from rsyncfns import (
    CHKDIR, FROMDIR, RSYNC, TODIR,
    build_rsyncd_conf, checkit, hands_setup, run_rsync,
)


conf = build_rsyncd_conf()
os.environ['RSYNC_CONNECT_PROG'] = f"{RSYNC} --config={conf} --daemon"

hands_setup()

# chkdir: vanilla copy minus the daemon's global "foobar.baz" exclude.
run_rsync('-av', '--exclude=foobar.baz', f'{FROMDIR}/', f'{CHKDIR}/')

checkit(
    ['-avvvvzz', 'localhost::test-from/', f'{TODIR}/'],
    CHKDIR, TODIR,
    allowed_codes=(0, 23),
)

#!/usr/bin/env python3
# Python rewrite of testsuite/daemon-gzip-upload.test.
#
# Upload a file tree over a compressed connection to a test daemon.
# Exercises (exorcises?) a bug in 2.5.3 that mis-handled doubly-compressed
# transfers. Uses the secure stdio-pipe transport by default; --use-tcp
# runs it against a real loopback rsyncd.

from rsyncfns import (
    CHKDIR, FROMDIR, TODIR,
    build_rsyncd_conf, checkit, hands_setup, run_rsync, start_test_daemon,
)


DAEMON_PORT = 12880

conf = build_rsyncd_conf()
hands_setup()

# chkdir: vanilla copy minus the daemon's global "foobar.baz" exclude.
run_rsync('-av', '--exclude=foobar.baz', f'{FROMDIR}/', f'{CHKDIR}/')

url = start_test_daemon(conf, DAEMON_PORT)

checkit(
    ['-avvvvzz', f'{FROMDIR}/', f'{url}test-to/'],
    CHKDIR, TODIR,
    allowed_codes=(0, 23),
)

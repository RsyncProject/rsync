#!/usr/bin/env python3
# Python rewrite of testsuite/daemon-refuse-compress.test.
#
# A daemon module configured with "refuse options = compress" must
# reject clients that ask for compression, and still serve the same
# transfer when the client does not.

import subprocess

from rsyncfns import (
    CHKDIR, FROMDIR, SCRATCHDIR, TODIR,
    build_rsyncd_conf, checkit, hands_setup, rmtree,
    rsync_argv, run_rsync, start_test_daemon, test_fail,
)


DAEMON_PORT = 12876

conf = build_rsyncd_conf()
# Append an extra module that refuses --compress (-z).
with open(conf, 'a') as f:
    f.write(f"""
[no-compress]
\tpath = {FROMDIR}
\tread only = yes
\trefuse options = compress
""")

hands_setup()
run_rsync('-av', '--exclude=foobar.baz', f'{FROMDIR}/', f'{CHKDIR}/')

url = start_test_daemon(conf, DAEMON_PORT) + 'no-compress/'

# A compressed transfer must be refused.
errlog = SCRATCHDIR / 'refuse.err'
proc = subprocess.run(
    rsync_argv('-avz', url, f'{TODIR}/'),
    stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
)
errlog.write_text(proc.stderr)
if proc.returncode == 0:
    print(proc.stderr)
    test_fail("compressed transfer was not refused")
if '--compress' not in proc.stderr:
    print(proc.stderr)
    test_fail("expected refuse error mentioning --compress")

# The same transfer without -z must succeed.
rmtree(TODIR)
TODIR.mkdir()
checkit(['-av', url, f'{TODIR}/'], CHKDIR, TODIR,
        allowed_codes=(0, 23))

#!/usr/bin/env python3

import subprocess

from harness.rsync import (
    CHKDIR, FROMDIR, SCRATCHDIR, TODIR,
    build_rsyncd_conf, checkit, hands_setup, rmtree,
    rsync_argv, run_rsync, start_test_daemon, test_fail,
)
from harness import metadata

metadata(features={'compression', 'daemon'}, transports={'pipe', 'tcp'}, min_peer='2.6.0', mutates={'filesystem', 'process', 'socket'}, tags={'configuration', 'daemon', 'version-mix'})

DAEMON_PORT = 12876

conf = build_rsyncd_conf()
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

rmtree(TODIR)
TODIR.mkdir()
checkit(['-av', url, f'{TODIR}/'], CHKDIR, TODIR,
        allowed_codes=(0, 23))

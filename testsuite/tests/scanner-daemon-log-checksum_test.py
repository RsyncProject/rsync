#!/usr/bin/python3
# Regression test for run5 0025: a daemon-as-sender with -c and a module
# `log format` containing %C read F_SUM(file) in log_formatted(), but
# start_server() never set sender_keeps_checksum, so make_file() reserved no
# SUM_EXTRA_CNT and F_SUM() read past the pool slot (hex-encoding adjacent heap
# into the transfer log).  Pull with -c from a %C-logging module; the daemon
# child must not crash (RED under ASAN before the fix).
import subprocess
from rsyncfns import (
    SCRATCHDIR, make_tree, makepath, rmtree, rsync_argv,
    start_test_daemon, test_fail, write_daemon_conf,
)

DAEMON_PORT = 12894

mod = SCRATCHDIR / 'csum-mod'
rmtree(mod)
makepath(mod)
make_tree(mod, depth=2, data=True)

conf = write_daemon_conf([
    ('csum', {'path': str(mod), 'read only': 'yes',
              # The module `log format` is only installed when transfer logging
              # is on (clientserver.c lp_transfer_logging gate); without this the
              # %C path -- the whole point of this test -- never runs.
              'transfer logging': 'yes', 'log format': '%o %C %f %l'}),
])
url = start_test_daemon(conf, DAEMON_PORT).rstrip('/')

dest = SCRATCHDIR / 'csum-pull'
rmtree(dest)
makepath(dest)
# Daemon is the SENDER; -c forces always_checksum so log_formatted()'s %C reads
# F_SUM(file) in the daemon child.
r = subprocess.run(rsync_argv('-a', '-c', f'{url}/csum/', f'{dest}/'),
                   capture_output=True, text=True)
if r.returncode < 0 or r.returncode >= 128:
    test_fail(f'daemon-as-sender -c with %C crashed (rc={r.returncode}): '
              f'{r.stderr.strip()[:200]}')
if not any(dest.rglob('f*')):
    test_fail(f'pull transferred no files (rc={r.returncode}): {r.stderr.strip()[:200]}')
print("scanner-daemon-log-checksum: daemon-as-sender -c with %C log format is clean")

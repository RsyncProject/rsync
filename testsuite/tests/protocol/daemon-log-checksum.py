#!/usr/bin/python3

import subprocess
from harness.daemon_config import write_daemon_conf
from harness.rsync import (
    SCRATCHDIR, make_tree, makepath, rmtree, rsync_argv, start_test_daemon, test_fail,
)

DAEMON_PORT = 12894

mod = SCRATCHDIR / 'csum-mod'
rmtree(mod)
makepath(mod)
make_tree(mod, depth=2, data=True)

conf = write_daemon_conf([
    ('csum', {'path': str(mod), 'read only': 'yes',
              'transfer logging': 'yes', 'log format': '%o %C %f %l'}),
])
url = start_test_daemon(conf, DAEMON_PORT).rstrip('/')

dest = SCRATCHDIR / 'csum-pull'
rmtree(dest)
makepath(dest)
r = subprocess.run(rsync_argv('-a', '-c', f'{url}/csum/', f'{dest}/'),
                   capture_output=True, text=True)
if r.returncode < 0 or r.returncode >= 128:
    test_fail(f'daemon-as-sender -c with %C crashed (rc={r.returncode}): '
              f'{r.stderr.strip()[:200]}')
if not any(dest.rglob('f*')):
    test_fail(f'pull transferred no files (rc={r.returncode}): {r.stderr.strip()[:200]}')
print('daemon checksum logging verified')

#!/usr/bin/env python3

import subprocess

from harness.daemon_config import write_daemon_conf
from harness.rsync import SCRATCHDIR, makepath, rmtree, rsync_argv, start_test_daemon, test_fail

DAEMON_PORT = 12894

base = SCRATCHDIR / 'kid48'
rmtree(base)
mod = base / 'mod'
sibling = base / 'sibling'
makepath(mod)
makepath(sibling)
(sibling / 'probe').write_text('out-of-tree basis content\n')

src = base / 'src'
makepath(src)
(src / 'probe').write_text('client content\n')

conf = write_daemon_conf([
    ('mod', {'path': str(mod), 'read only': 'no'}),
])
url = start_test_daemon(conf, DAEMON_PORT)

proc = subprocess.run(
    rsync_argv('-rv', '--link-dest=../sibling', f'{src}/', f'{url}mod/'),
    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
out = proc.stdout or ''

reached_outside = 'does not exist' not in out and 'is not a dir' not in out

if reached_outside:
    test_fail(
        "daemon reached outside the module via --link-dest=../sibling: the "
        "out-of-tree directory was stat'd (no 'arg does not exist' warning), "
        "leaking the existence of paths outside the module.\n--- client saw ---\n"
        + out)

print("daemon-link-dest-escape: --link-dest=../sibling was confined to the "
      "module (out-of-tree basis dir not reachable)")

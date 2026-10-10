#!/usr/bin/env python3

import subprocess

from harness.daemon_config import write_daemon_conf
from harness.rsync import SCRATCHDIR, makepath, rmtree, rsync_argv, start_test_daemon, test_fail

DAEMON_PORT = 12921

mod = SCRATCHDIR / 'srvmod'
rmtree(mod)
makepath(mod / 'secret')
(mod / 'public.txt').write_text("PUBLIC\n")
(mod / 'secret' / 'rules').write_text("- *\n")

conf = write_daemon_conf([
    ('mod', {'path': str(mod),
             'read only': 'yes',
             'filter': '- /secret/***'}),
])
url = start_test_daemon(conf, DAEMON_PORT)

def pull(args, dest):
    rmtree(dest)
    makepath(dest)
    return subprocess.run(rsync_argv('-a', *args, f'{url}mod/', f'{dest}/'),
                          stdout=subprocess.DEVNULL,
                          stderr=subprocess.PIPE, text=True)

base_dest = SCRATCHDIR / 'base'
rc = pull([], base_dest)
if rc.returncode not in (0, 23):
    test_fail(f"baseline pull failed (rc={rc.returncode}): {rc.stderr!r}")
if not (base_dest / 'public.txt').is_file():
    test_fail('baseline did not transfer public.txt')
if (base_dest / 'secret' / 'rules').exists():
    test_fail('baseline exposed /secret/rules')

atk_dest = SCRATCHDIR / 'atk'
rc = pull(['-M', '--filter=._/secret/rules'], atk_dest)
if (atk_dest / 'secret' / 'rules').exists():
    test_fail('daemon delivered hidden merge file /secret/rules')

if not (atk_dest / 'public.txt').is_file():
    test_fail('daemon parsed hidden merge file /secret/rules')

print('daemon filter refused the hidden merge file')

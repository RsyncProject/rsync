#!/usr/bin/env python3

import os

from harness.rsync import (
    SCRATCHDIR, get_rootgid, get_rootuid, get_testuid, makepath, rmtree,
    run_rsync, start_test_daemon, test_fail,
)

DAEMON_PORT = 12950
base = SCRATCHDIR / 'scan-desync'
rmtree(base)
mod = base / 'module'
makepath(mod / 'y', mod / 'x', mod / 'sub', mod / 'sibling')
(mod / 'y' / 'real.txt').write_text('shallow\n')
os.symlink('../y', mod / 'x' / 'jump')
(mod / 'sibling' / 's.txt').write_text('sibling\n')
os.symlink('../sibling', mod / 'sub' / 'climb')

root = get_testuid() == get_rootuid()
ids = f"uid = {get_rootuid()}\ngid = {get_rootgid()}" if root else ""

conf = base / 'd.conf'
conf.write_text(f"""\
pid file = {base}/rsyncd.pid
use chroot = no
{ids}
log file = {base}/rsyncd.log

[m]
    path = {mod}
    read only = yes
    hosts allow = 127.0.0.1
""")
url = start_test_daemon(conf, DAEMON_PORT)

for name, source, relative, content in (
    ('shallow', 'x/jump', 'real.txt', 'shallow\n'),
    ('subdir', 'sub', 'climb/s.txt', 'sibling\n'),
):
    destination = base / name
    destination.mkdir()
    run_rsync('-a', '--copy-dirlinks', f'{url}m/{source}/', f'{destination}/')
    result = destination / relative
    if not result.is_file() or result.read_text() != content:
        test_fail(f'{name}: daemon scan lost an in-module climbing symlink')

print('daemon scans remain anchored at the module root')

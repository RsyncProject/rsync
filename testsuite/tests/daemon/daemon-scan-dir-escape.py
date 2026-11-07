#!/usr/bin/env python3

import os

from harness.rsync import (
    SCRATCHDIR, get_rootgid, get_rootuid, get_testuid, makepath, rmtree,
    run_rsync, start_test_daemon, test_fail,
)

DAEMON_PORT = 12947
MARKER = "XFIL-OUTSIDE-MODULE-SYMLINK-TARGET"

base = SCRATCHDIR / 'daemon-scan-escape'
rmtree(base)
mod = base / 'mod'
outside = base / 'outside'
dest = base / 'dest'
makepath(mod / 'real', outside, dest)

(mod / 'real' / 'in.txt').write_text("in-module\n")

os.symlink('../outside', mod / 'escape')

os.symlink(MARKER, outside / 'xfil_link')
(outside / 'xfil_dir').mkdir()
(outside / 'xfil_dir' / 'inner').write_text("x\n")
(outside / 'xfil_file').write_text("x\n")

root = get_testuid() == get_rootuid()
ids = f"uid = {get_rootuid()}\ngid = {get_rootgid()}" if root else ""

conf = base / 'daemon-scan.conf'
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

run_rsync('-a', '--copy-dirlinks', f'{url}m/', f'{dest}/', check=False)

esc = dest / 'escape'
leak = None
link = esc / 'xfil_link'
if link.is_symlink() and os.readlink(link) == MARKER:
    leak = f"symlink target leaked: dest/escape/xfil_link -> {MARKER}"
else:
    for name in ('xfil_dir', 'xfil_file', 'xfil_link'):
        if (esc / name).exists() or (esc / name).is_symlink():
            leak = f"out-of-module name leaked: dest/escape/{name}"
            break

if leak is not None:
    test_fail(f'--copy-dirlinks escaped the daemon module: {leak}')
print("daemon-scan-dir-escape: --copy-dirlinks enumeration stayed within the module")

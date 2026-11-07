#!/usr/bin/env python3

import os
import subprocess

from harness.daemon_config import write_daemon_conf
from harness.rsync import (
    SCRATCHDIR, makepath, rmtree, rsync_argv, start_test_daemon, test_fail, test_skipped,
)

DAEMON_PORT = 12923

if os.geteuid() == 0:
    import grp
    usable = []
    for gr in grp.getgrall():
        if gr.gr_gid not in usable:
            usable.append(gr.gr_gid)
    if len(usable) < 2:
        test_skipped("need >=2 groups defined on the system")
else:
    usable = []
    for g in [os.getgid()] + list(os.getgroups()):
        if g not in usable:
            usable.append(g)
    if len(usable) < 2:
        test_skipped("need >=2 groups the test user belongs to")
src_gid, dst_gid = usable[0], usable[1]

moddir = SCRATCHDIR / 'gmod'
srcdir = SCRATCHDIR / 'gsrc'
makepath(moddir)

conf = write_daemon_conf([('gmod', {'path': str(moddir), 'read only': 'no'})])
url = start_test_daemon(conf, DAEMON_PORT) + 'gmod/'

def check(label, *extra_opts):
    rmtree(moddir)
    rmtree(srcdir)
    makepath(moddir)
    makepath(srcdir)
    f = srcdir / 'f.dat'
    f.write_text("hi\n")
    os.chown(f, -1, src_gid)

    proc = subprocess.run(
        rsync_argv('-rg', *extra_opts, f'--groupmap=*:{dst_gid}', str(f), url),
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if proc.returncode != 0:
        print(proc.stdout)
        test_fail(f"[{label}] groupmap upload failed (rc={proc.returncode})")

    got = os.stat(moddir / 'f.dat').st_gid
    if got != dst_gid:
        test_fail(f"[{label}] --groupmap='*:{dst_gid}' wildcard ignored over "
                  f"daemon: got gid {got}, expected {dst_gid} (regression of #829)")

check('default-args')
check('secluded-args', '--secluded-args')

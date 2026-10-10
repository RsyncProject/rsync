#!/usr/bin/env python3

import os
import subprocess

from harness.rsync import (
    SCRATCHDIR,
    rsync_argv, get_testuid, get_rootuid, get_rootgid,
    rmtree, start_test_daemon, test_fail,
)
from harness import metadata

metadata(features={'daemon', 'symlink'}, transports={'pipe', 'tcp'}, min_peer='2.6.0', mutates={'filesystem', 'process', 'socket'}, tags={'daemon', 'security', 'version-mix'})

DAEMON_PORT = 12881

mod = SCRATCHDIR / 'module'
outside = SCRATCHDIR / 'outside'
listfile = SCRATCHDIR / 'listed.txt'
conf = SCRATCHDIR / 'test-rsyncd.conf'

rmtree(mod)
rmtree(outside)
mod.mkdir(parents=True)
outside.mkdir(parents=True)

(outside / 'leak_marker.txt').write_text(
    "OUTSIDE_PROTECTED_FILE_USED_AS_LEAK_DETECTOR\n"
)
os.chmod(outside / 'leak_marker.txt', 0o644)

os.symlink(str(outside), mod / 'cd')

(mod / 'realdir').mkdir()
(mod / 'realdir' / 'in_module.txt').write_text("INSIDE_THE_MODULE\n")

my_uid = get_testuid()
root_uid = get_rootuid()
root_gid = get_rootgid()
uid_line = f"uid = {root_uid}"
gid_line = f"gid = {root_gid}"
if my_uid != root_uid:
    uid_line = '#' + uid_line
    gid_line = '#' + gid_line

conf.write_text(f"""\
use chroot = no
{uid_line}
{gid_line}
log file = {SCRATCHDIR}/rsyncd.log
[upload]
    path = {mod}
    use chroot = no
    read only = no
""")

url = start_test_daemon(conf, DAEMON_PORT)

ctl = subprocess.run(
    rsync_argv('-nrv', f'{url}upload/realdir/', f'{SCRATCHDIR}/dst/'),
    capture_output=True, text=True,
)
if ctl.returncode != 0 or 'in_module.txt' not in ctl.stdout:
    test_fail("positive control: listing an in-module path did not enumerate "
              f"in_module.txt (rc={ctl.returncode}); leak check would be vacuous"
              f"\n{ctl.stdout}{ctl.stderr}")

proc = subprocess.run(
    rsync_argv('-nrv', f'{url}upload/cd/', f'{SCRATCHDIR}/dst/'),
    capture_output=True, text=True,
)
if proc.returncode >= 128:
    test_fail(f"leak pull: rsync died from a signal (rc={proc.returncode})")
listfile.write_text(proc.stdout + proc.stderr)

if 'leak_marker.txt' in listfile.read_text():
    import sys
    sys.stderr.write("----- leaked listing follows\n")
    for line in listfile.read_text().splitlines():
        sys.stderr.write(f"    {line}\n")
    sys.stderr.write("----- leaked listing ends\n")
    test_fail(
        "sender flist leak: outside/leak_marker.txt was enumerated to "
        "the client (daemon's chdir followed the cd symlink during flist "
        "generation)"
    )

#!/usr/bin/env python3
# Python rewrite of testsuite/copy-dest-source-symlink.test.
#
# Regression test for codex audit Finding 3a: copy_file()'s source open
# in copy_altdest_file() is via do_open_nofollow(), which only refuses
# a final-component symlink. A daemon-module attacker who plants a
# parent symlink (module/cd -> /outside) then runs --copy-dest=cd
# against a source matching the size+mtime of /outside/target.txt
# drives the receiver to read /outside/target.txt under the daemon's
# authority and copy it into the module.
#
# Detection: source and outside have identical metadata (size, mtime,
# mode) but distinct content. After the transfer, module/target.txt
# must contain source's content, not outside's.

import filecmp
import os
import subprocess

from rsyncfns import (
    SCRATCHDIR,
    rsync_argv, get_testuid, get_rootuid, get_rootgid,
    rmtree, start_test_daemon, test_fail,
)


DAEMON_PORT = 12883


mod = SCRATCHDIR / 'module'
outside = SCRATCHDIR / 'outside'
src_dir = SCRATCHDIR / 'src_files'
conf = SCRATCHDIR / 'test-rsyncd.conf'

for d in (mod, outside, src_dir):
    rmtree(d)
    d.mkdir(parents=True)

(outside / 'target.txt').write_text("OUTSIDE_LEAKED_DATA!\n")
os.chmod(outside / 'target.txt', 0o644)

os.symlink(str(outside), mod / 'cd')

# Source: same size + mtime + mode as outside, different content.
(src_dir / 'target.txt').write_text("ATTACKER_KNOWN_DATA!\n")
ref = (outside / 'target.txt').stat()
os.utime(src_dir / 'target.txt', (ref.st_atime, ref.st_mtime))
os.chmod(src_dir / 'target.txt', 0o644)

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

subprocess.run(
    rsync_argv('-rtp', '--copy-dest=cd',
               f'{src_dir}/', f'{url}upload/'),
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
)

target = mod / 'target.txt'
if not target.is_file():
    test_fail(
        "destination file was not created -- daemon transfer failed "
        "before the test could observe the basedir behaviour"
    )

if filecmp.cmp(target, outside / 'target.txt', shallow=False):
    test_fail(
        "basedir-escape via copy_file source: module/target.txt now "
        "contains the contents of outside/target.txt -- daemon read "
        "/outside via the cd symlink and copied it into the module"
    )
if not filecmp.cmp(target, src_dir / 'target.txt', shallow=False):
    test_fail(
        "destination doesn't match source content (and isn't outside "
        "content either): unexpected state"
    )

#!/usr/bin/env python3
# Python rewrite of testsuite/alt-dest-symlink-race.test.
#
# Regression test for the basedir-confinement gap in
# secure_relative_open(): a parent symlink ON basedir is followed
# unrestrictedly, then RESOLVE_BENEATH is applied only to relpath,
# anchored at the wrong directory.  In daemon mode this lets a local
# attacker who can write into a module plant module/cd -> /outside and
# then use --link-dest=cd / --copy-dest=cd / --compare-dest=cd to make
# the receiver's basis-file lookup resolve into /outside, leaking
# daemon-readable content via the rsync delta-rolling read-disclosure
# primitive.
#
# Detection: with --link-dest, when basis matches source exactly the
# destination is hard-linked to the basis. On a successful escape the
# destination shares an inode with /outside/target.txt; on a fix it
# doesn't.

import os
import subprocess

from rsyncfns import (
    SCRATCHDIR,
    rsync_argv, get_testuid, get_rootuid, get_rootgid,
    rmtree, start_test_daemon, test_fail,
)


DAEMON_PORT = 12882


mod = SCRATCHDIR / 'module'
outside = SCRATCHDIR / 'outside'
src_dir = SCRATCHDIR / 'src_files'
conf = SCRATCHDIR / 'test-rsyncd.conf'

for d in (mod, outside, src_dir):
    rmtree(d)
    d.mkdir(parents=True)

# The outside file an attacker wants the daemon to treat as a basis.
(outside / 'target.txt').write_text("OUTSIDE_SECRET_DATA\n")
os.chmod(outside / 'target.txt', 0o644)

# Attacker-planted module symlink.
os.symlink(str(outside), mod / 'cd')

# Source: same content + mtime + mode as outside, so --link-dest hard-
# links the destination to the basis iff basedir lookup escapes.
(src_dir / 'target.txt').write_text("OUTSIDE_SECRET_DATA\n")
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

# Push directly into the module root: pushing into a destination subdir
# would make the receiver chdir into it before resolving --link-dest,
# making "cd" resolve in the wrong CWD and masking the bug.
subprocess.run(
    rsync_argv('-rtp', '--link-dest=cd',
               f'{src_dir}/', f'{url}upload/'),
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
)

target = mod / 'target.txt'
if not target.is_file():
    test_fail(
        "destination file was not created -- daemon transfer failed "
        "before the test could observe the basedir behaviour"
    )

if target.stat().st_ino == (outside / 'target.txt').stat().st_ino:
    test_fail(
        f"basedir-escape: --link-dest hard-linked module/target.txt to "
        f"outside/target.txt (inode {target.stat().st_ino}); daemon's "
        "basis-file lookup followed the parent symlink on the basedir"
    )

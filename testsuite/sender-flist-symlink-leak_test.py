#!/usr/bin/env python3
# Python rewrite of testsuite/sender-flist-symlink-leak.test.
#
# Regression test for codex re-check finding: the sender-side file-list
# generator could still follow an attacker-planted symlink out of the
# module via change_pathname() -> change_dir(...,CD_SKIP_CHDIR) ->
# change_dir(...,CD_NORMAL).  Reach: a daemon module with use chroot =
# no, attacker plants module/cd -> /outside, client pulls
# rsync://daemon/module/cd/; the daemon would enumerate /outside in
# the file list (metadata leak) before the actual content transfer
# failed at secure_relative_open.

import os
import platform
import subprocess

from rsyncfns import (
    SCRATCHDIR,
    rsync_argv, get_testuid, get_rootuid, get_rootgid,
    rmtree, start_test_daemon, test_fail, test_skipped,
)


DAEMON_PORT = 12881


# Platforms without RESOLVE_BENEATH equivalents fall back to a per-
# component walk that this test is not in scope for.
if platform.system() in ('SunOS', 'OpenBSD', 'NetBSD') or platform.system().startswith('CYGWIN'):
    test_skipped(
        f"secure change_dir relies on RESOLVE_BENEATH-equivalent kernel "
        f"support not available on {platform.system()}"
    )

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

# A legitimate in-module file, used as a positive control so the leak check
# below can't pass simply because the daemon's listing machinery is broken.
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

# Positive control: a normal recursive listing of an in-module path must
# enumerate the in-module file. If this fails, the daemon's flist generation is
# broken and the leak check below would be vacuously satisfied.
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

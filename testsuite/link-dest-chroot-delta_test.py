#!/usr/bin/env python3
# Regression test for the chroot-daemon facet of the issue #915 /
# CVE-2026-29518 fallout.
#
# The CVE symlink-race hardening routed the receiver's alt-dest basis
# open (secure_basis_open in receiver.c) through secure_relative_open(),
# whose front door rejects a relative ".." basedir.  That broke the
# DELTA path for --link-dest=../01 (a sibling backup) under a
# "use chroot = yes" daemon -- the default deployment -- as well as the
# no-chroot daemon (issue #915) and local --no-whole-file transfers.
#
# Under "use chroot = yes" the per-module chroot makes the module the
# filesystem root, module_dirlen is 0, and the alt-dest reaches the
# receiver as a literal "../01"; with am_chrooted=1 the receiver chdir's
# into the destination "/00" and resolves "../01" -> "/01" (still inside
# the chroot).  The unconditional confinement rejected that ".." and a
# changed file delta'd against the basis aborted the whole transfer with
# "got a block match with no basis file".  rsync 3.4.1 (pre-hardening)
# handled it.
#
# The fix gives secure_basis_open() the same gate the do_*_at() wrappers
# already use: in a chroot (am_chrooted) the kernel root is the module
# boundary, so the basis is opened with a bare do_open() that resolves
# against the CWD -- exactly as before the hardening.
#
# This is the only test that exercises the am_chrooted code path.  It
# needs CAP_SYS_CHROOT *and* a privilege drop that can call setgroups()
# (the use-chroot=yes daemon drops to its module uid/gid).  Real root has
# both; an unprivileged user namespace needs a *range* gid mapping
# (--map-auto) for setgroups to be permitted -- plain --map-root-user is
# not enough.  We re-exec under unshare when we can't chroot directly,
# and skip if neither is available (matching daemon-chroot-acl_test.py
# and testsuite/COVERAGE.md's "use chroot = yes ... needs root").

import os
import platform
import shutil
import subprocess
import sys

from rsyncfns import (
    SCRATCHDIR,
    rsync_argv, rmtree, start_test_daemon, test_fail, test_skipped,
)


if platform.system() != 'Linux':
    test_skipped("test is Linux-specific (uses chroot+unshare)")


def _can_chroot() -> bool:
    proc = subprocess.run(['chroot', '/', '/bin/true'],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return proc.returncode == 0


if not _can_chroot():
    if not os.environ.get('RSYNC_UNSHARED'):
        unshare = shutil.which('unshare')
        if unshare is not None:
            # --map-auto adds the user's subuid/subgid ranges, which is what
            # lets the daemon's setgroups() during privilege drop succeed.
            argv = [unshare, '--user', '--map-root-user', '--map-auto']
            if subprocess.run(argv + ['true'], capture_output=True).returncode == 0:
                print("Re-running under unshare --user --map-root-user --map-auto...")
                env = os.environ.copy()
                env['RSYNC_UNSHARED'] = '1'
                os.execvpe(unshare, argv + [sys.executable, __file__], env)
    test_skipped("need CAP_SYS_CHROOT + setgroups (real root, or "
                 "unshare --user --map-root-user --map-auto)")


DAEMON_PORT = 12916

mod = SCRATCHDIR / 'module'
conf = SCRATCHDIR / 'test-rsyncd.conf'
src = SCRATCHDIR / 'src_files'

for d in (mod, src):
    rmtree(d)
    d.mkdir(parents=True)
(mod / '01').mkdir()   # the sibling "previous backup"
(mod / '00').mkdir()   # the destination subdir

# Large files differing by one byte, with DISTINCT mtimes so the quick-check
# does NOT treat them as identical (which would hard-link instead of forcing
# the delta/basis-open path this test is about).
basis = mod / '01' / 'f'
source = src / 'f'
basis.write_text('A' * 200000 + 'X')
source.write_text('A' * 200000 + 'Y')
os.utime(basis, (1_000_000_000, 1_000_000_000))   # 2001
os.utime(source, (1_700_000_000, 1_700_000_000))  # 2023

# We only reach here as (real or namespace) root, so the daemon can chroot
# and drop to uid/gid 0.
conf.write_text(f"""\
use chroot = yes
uid = 0
gid = 0
log file = {SCRATCHDIR}/rsyncd.log
[bak]
    path = {mod}
    use chroot = yes
    read only = no
""")

url = start_test_daemon(conf, DAEMON_PORT)

# After the per-module chroot the module root is "/", so the destination is
# "/00" and the sibling basis "--link-dest=../01" -> "/01", inside the chroot.
proc = subprocess.run(
    rsync_argv('-rt', '--no-whole-file', '--link-dest=../01',
               f'{src}/', f'{url}bak/00/'),
    stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
)
if proc.returncode != 0:
    test_fail(
        "use-chroot=yes --link-dest=../01 delta aborted (rc="
        f"{proc.returncode}): the receiver could not open the sibling basis "
        "inside the chroot for the delta transfer.  stderr:\n{}".format(proc.stderr))

dest = mod / '00' / 'f'
if not dest.is_file():
    test_fail("use-chroot=yes --link-dest=../01 delta produced no destination file")
if dest.read_text() != source.read_text():
    test_fail(
        "use-chroot=yes --link-dest=../01 delta reconstructed the wrong "
        "content: the destination does not match the source after a delta "
        "against the sibling basis inside the chroot")

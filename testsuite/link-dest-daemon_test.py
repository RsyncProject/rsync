#!/usr/bin/env python3
# Regression test for issue #915.
#
# After the CVE-2026-29518 / CVE-2026-43619 symlink-race hardening, a
# "use chroot = no" daemon stopped honouring --link-dest=../01 -- the
# standard rotating-backup layout, where the previous backup is a sibling
# of the destination subdir (push into .../00 with --link-dest=../01,
# pointing at .../01).  The receiver chdir's into the destination subdir,
# and secure_relative_open()'s confinement (RESOLVE_BENEATH anchored
# there) refused the ".." climb even though ../01 is still inside the
# module.  No basis was found, every file was re-transferred instead of
# hard-linked, and backup disks silently filled up.
#
# The fix re-anchors the confinement at the module root, so an in-module
# ".." climb resolves while an escape out of the module is still rejected.
#
# Two cases are exercised:
#   1. functional -- --link-dest=../01 into a destination subdir must
#      hard-link the unchanged file;
#   2. security  -- when the sibling basis is a symlink pointing OUT of
#      the module, the basis-file lookup must NOT follow it out (the
#      runtime-symlink TOCTOU the CVE is about; this complements
#      alt-dest-symlink-race_test.py, which covers pushing into the
#      module root rather than a subdir).

import os
import subprocess

from rsyncfns import (
    SCRATCHDIR,
    rsync_argv, get_testuid, get_rootuid, get_rootgid,
    rmtree, start_test_daemon, test_fail,
)


DAEMON_PORT = 12915


mod = SCRATCHDIR / 'module'
outside = SCRATCHDIR / 'outside'
src_files = SCRATCHDIR / "src_files"
conf = SCRATCHDIR / 'test-rsyncd.conf'

for d in (mod, outside, src_files):
    rmtree(d)
    d.mkdir(parents=True)

# The single source file used for every case.
(src_files / 'f').write_text("hello world\n")
os.chmod(src_files / 'f', 0o644)
src_ref = (src_files / 'f').stat()


def seed_basis(path):
    """Create a previous-backup copy identical in content, mtime and mode to
    src_files/f, so that an exact --link-dest match hard-links the destination to
    it (and, for the escape case, would hard-link to the outside file)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("hello world\n")
    os.chmod(path, 0o644)
    os.utime(path, (src_ref.st_atime, src_ref.st_mtime))


# Case 1: module/00 (destination) and module/01 (real sibling basis).
(mod / '00').mkdir()
seed_basis(mod / '01' / 'f')

# Case 2: module/sym00 (destination) and module/sym01 -> outside, a symlink
# escaping the module. outside/f matches src_files exactly, so a followed symlink
# would hard-link the destination to it (and be detected).
(mod / 'sym00').mkdir()
seed_basis(outside / 'f')
os.symlink(str(outside), mod / 'sym01')

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
[bak]
    path = {mod}
    use chroot = no
    read only = no
""")

url = start_test_daemon(conf, DAEMON_PORT)


def push(link_dest, dest_subdir):
    subprocess.run(
        rsync_argv('-rtp', f'--link-dest={link_dest}',
                   f'{src_files}/', f'{url}bak/{dest_subdir}/'),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


# 1. Functional: the in-module ".." climb to a sibling backup must hard-link.
push('../01', '00')
dest = mod / '00' / 'f'
if not dest.is_file():
    test_fail("daemon transfer failed before the test could observe --link-dest")
if dest.stat().st_ino != (mod / '01' / 'f').stat().st_ino:
    test_fail(
        "issue #915: --link-dest=../01 via a use-chroot=no daemon did not "
        "hard-link the unchanged file (it was re-transferred); the in-module "
        "'..' climb to the sibling backup was wrongly rejected by the "
        "basis-file confinement")

# 2. Security: the sibling is a symlink out of the module; the basis-file
#    lookup must not follow it out (must not hard-link to the outside file).
push('../sym01', 'sym00')
sdest = mod / 'sym00' / 'f'
if sdest.is_file() and sdest.stat().st_ino == (outside / 'f').stat().st_ino:
    test_fail(
        "basedir-escape: --link-dest=../sym01 hard-linked the destination to "
        f"outside/f (inode {sdest.stat().st_ino}); the basis-file lookup "
        "followed a symlink out of the module")

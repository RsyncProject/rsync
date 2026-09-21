#!/usr/bin/env python3
# Symlink-race (TOCTOU) LPE in the receiver's ACL-apply path.
#
# rsync applies a received ACL with the path-based sys_acl_set_file() ->
# acl_set_file(), which follows symlinks at the final component (POSIX has no
# AT_SYMLINK_NOFOLLOW for ACLs) and -- unlike do_chmod_at()/do_lchown_at() --
# has no fd-held/secure wrapper. In a "use chroot = no" daemon there is a
# window between do_mknod_at() creating a FIFO and set_acl() applying its ACL.
# A local attacker who flips the leaf fifo <-> symlink->privileged-file in that
# window gets the source FIFO's ACL written to the symlink target: an LPE, since
# the attacker chooses the ACL (e.g. grant themselves rwx on a root-owned file).
#
# The source FIFO carries a marker entry user:MARKER_UID:rwx; the test asserts
# the outside privileged sentinel never gains that entry no matter how the race
# lands. RED on the unhardened tree; GREEN once the ACL set goes through a held
# fd that can't be redirected by a symlink.

import os
import platform
import shutil
import subprocess
import time

from rsyncfns import (
    SCRATCHDIR, race_budget,
    forced_protocol, get_rootgid, get_rootuid, get_testuid,
    rmtree, run_rsync, rsync_argv, start_path_flipper, stop_flipper,
    start_test_daemon, test_fail, test_skipped,
)

DAEMON_PORT = 12896
MARKER_UID = 60001          # distinctive; must not appear in the baseline ACL


# ---- gates -----------------------------------------------------------------
if platform.system() != 'Linux':
    test_skipped("POSIX ACL race test is Linux-only (setfacl/getfacl semantics)")
if not shutil.which('setfacl') or not shutil.which('getfacl'):
    test_skipped("setfacl/getfacl not available")
# Protocol gate FIRST: this test transfers an ACL over the wire, which requires
# protocol 30+ regardless of the host's ACL capability.  It MUST precede the
# ACL_at check below: on a no-xattrat box that check short-circuits to a PASS,
# so if it ran first a proto-29 run would PASS where an xattrat box SKIPs --
# diverging from the kernel-independent RSYNC_EXPECT_SKIPPED check29 list and
# failing CI on the no-xattrat Ubuntu runners.
proto = forced_protocol()
if proto is not None and proto < 30:
    test_skipped(f"ACL transfer requires protocol 30+ (negotiated {proto})")
vv = run_rsync('-VV', check=True, capture_output=True)
if '"ACLs": true' not in vv.stdout:
    test_skipped("rsync built without ACL support")
if '"ACL_at": true' not in vv.stdout:
    # No race-safe ACL primitive on this kernel (no *xattrat, pre-6.13 / BSD):
    # by project policy a received ACL is then applied via the path so --acls
    # keeps working, which re-opens this race.  The race is an accepted residual
    # on platforms that cannot offer the secure primitive, so there is nothing
    # to assert here.  We PASS (rather than skip) so the proto-30+ skip set stays
    # kernel-independent (a no-xattrat and an xattrat box behave the same here).
    print("acl-symlink-race: no race-safe ACL primitive on this platform; "
          "--acls is functional-but-racy here by policy (race not asserted)")
    raise SystemExit(0)


def setfacl(spec, path):
    if subprocess.run(['setfacl', '-m', spec, str(path)]).returncode != 0:
        test_skipped("filesystem has ACLs disabled (setfacl failed)")


def getfacl(path) -> str:
    # Plain getfacl only: the suite runs with POSIXLY_CORRECT=1, under which
    # getfacl rejects -c/-E and accepts just the file argument.
    return subprocess.run(['getfacl', str(path)],
                          capture_output=True, text=True).stdout


def facl_has_marker(path) -> bool:
    return f'user:{MARKER_UID}:' in getfacl(path)


# ---- layout ----------------------------------------------------------------
mod = SCRATCHDIR / 'module'
outside = SCRATCHDIR / 'outside'
src = SCRATCHDIR / 'src_files'
conf = SCRATCHDIR / 'test-rsyncd.conf'

for d in (mod, outside, src):
    rmtree(d)
    d.mkdir(parents=True)

# The privileged file the attacker wants to grant themselves access to.
priv = outside / 'privileged'
priv.write_text("ROOT_OWNED_SECRET\n")
os.chmod(priv, 0o600)
if facl_has_marker(priv):
    test_fail("baseline outside file already carries the marker ACL")

# Source FIFO carrying the attacker-chosen marker ACL.
victim_src = src / 'victim'
os.mkfifo(victim_src, 0o600)
setfacl(f'user:{MARKER_UID}:rwx', victim_src)

# Attacker's symlink inside the module, pointing at the privileged file.
os.symlink(str(priv), mod / '.evil')

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


def push():
    subprocess.run(
        rsync_argv('-rtpA', '--specials', f'{src}/', f'{url}upload/'),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


# ---- positive control: a normal push carries the FIFO's ACL into the module,
# so the race below isn't vacuous (ACLs really are being applied). ------------
push()
landed = mod / 'victim'
if not facl_has_marker(landed):
    test_fail("positive control: a normal push did not apply the FIFO's ACL in "
              "the module -- the daemon isn't preserving special-file ACLs, so "
              "the race scenario would be vacuous")
# Clean the control artifact so the flipper starts from a known state.  It is a
# FIFO: use unlink, not rmtree -- shutil.rmtree on a pre-3.11 Python does
# os.open(path, O_RDONLY) on the target, which blocks forever on a writerless
# FIFO (the daemon-side hang seen on Ubuntu 22.04's Python 3.10).
landed.unlink()


# ---- the race --------------------------------------------------------------
# Flip the destination leaf between the FIFO rsync creates and the symlink to
# the privileged file. If set_acl() resolves the leaf while it is the symlink,
# the marker ACL lands on the privileged file.
flip = start_path_flipper(mod / 'victim', mod / '.evil')
deadline = time.monotonic() + race_budget(10.0)
try:
    while time.monotonic() < deadline:
        push()
        if facl_has_marker(priv):
            test_fail(
                f"ACL-set symlink race: the FIFO's user:{MARKER_UID}:rwx ACL was "
                "applied to the outside privileged file -- the receiver's "
                "set_acl() followed the leaf symlink (LPE). "
                f"getfacl {priv}:\n" + getfacl(priv)
            )
finally:
    stop_flipper(flip)

# No escape within the race budget.

#!/usr/bin/env python3

import os
import platform
import shutil
import subprocess
import time

from harness.mutation import race_budget, start_path_flipper, stop_flipper
from harness.rsync import (
    SCRATCHDIR, acl_get, acl_set, forced_protocol, get_rootgid, get_rootuid, get_testuid,
    rmtree, rsync_argv, run_rsync, start_test_daemon, test_fail, test_skipped,
)

DAEMON_PORT = 12896
MARKER_UID = 60001

if platform.system() != 'Linux':
    test_skipped("POSIX ACL race test is Linux-only (setfacl/getfacl semantics)",
                 capability='linux_acl')
if not shutil.which('setfacl') or not shutil.which('getfacl'):
    test_skipped("setfacl/getfacl not available", capability='linux_acl')
proto = forced_protocol()
if proto is not None and proto < 30:
    test_skipped(f"ACL transfer requires protocol 30+ (negotiated {proto})",
                 capability='protocol_30')
vv = run_rsync('-VV', check=True, capture_output=True)
if '"ACLs": true' not in vv.stdout:
    test_skipped("rsync built without ACL support", capability='linux_acl')
if '"ACL_at": true' not in vv.stdout:
    print("acl-symlink-race: no race-safe ACL primitive on this platform; "
          "--acls is functional-but-racy here by policy (race not asserted)")
    raise SystemExit(0)

def facl_has_marker(path) -> bool:
    return f'user:{MARKER_UID}:' in acl_get(path)

mod = SCRATCHDIR / 'module'
outside = SCRATCHDIR / 'outside'
src = SCRATCHDIR / 'src_files'
conf = SCRATCHDIR / 'test-rsyncd.conf'

for d in (mod, outside, src):
    rmtree(d)
    d.mkdir(parents=True)

priv = outside / 'privileged'
priv.write_text("ROOT_OWNED_SECRET\n")
os.chmod(priv, 0o600)
if facl_has_marker(priv):
    test_fail("baseline outside file already carries the marker ACL")

victim_src = src / 'victim'
os.mkfifo(victim_src, 0o600)
if not acl_set(f'user:{MARKER_UID}:rwx', victim_src):
    test_skipped('filesystem has ACLs disabled', capability='linux_acl')

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

push()
landed = mod / 'victim'
if not facl_has_marker(landed):
    test_fail("positive control: a normal push did not apply the FIFO's ACL in "
              "the module -- the daemon isn't preserving special-file ACLs, so "
              "the race scenario would be vacuous")
landed.unlink()

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

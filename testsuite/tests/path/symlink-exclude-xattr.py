#!/usr/bin/env python3

import os
import subprocess

from harness.daemon_config import write_daemon_conf
from harness.rsync import (
    SCRATCHDIR, forced_protocol, rmtree, rsync_argv, start_test_daemon, test_fail,
    test_skipped, xattrs_supported,
)

if not xattrs_supported() or not hasattr(os, 'getxattr') or not hasattr(os, 'setxattr'):
    test_skipped("user xattrs not available", capability='symlink_xattr')

proto = forced_protocol()
if proto is not None and proto < 30:
    test_skipped(f"xattr (-X) transfer requires protocol 30+ (negotiated {proto})",
                 capability='protocol_30')

DAEMON_PORT = 12992
XNAME = 'user.rsync.marker'

def getx(p):
    try:
        return os.getxattr(str(p), XNAME).decode()
    except OSError:
        return None

base = SCRATCHDIR / 'symlink-exclude-xattr'
rmtree(base)
base.mkdir()

mod = base / 'mod'
mod.mkdir()
pub = mod / 'pub'
pub.mkdir()
victim = pub / 'blocked'
victim.write_text("DATA\n")
try:
    os.setxattr(str(victim), XNAME, b'ORIGINAL')
except OSError:
    test_skipped("cannot set user xattr on this filesystem", capability='symlink_xattr')
os.symlink('pub/', mod / 'blink2')

src = base / 'src'
(src / 'blink2').mkdir(parents=True)
sf = src / 'blink2' / 'blocked'
sf.write_text("DATA\n")
os.setxattr(str(sf), XNAME, b'ATTACKER')

conf = write_daemon_conf(
    [('mod', {'path': str(mod), 'read only': 'no', 'exclude': '/pub/blocked'})])
url = start_test_daemon(conf, DAEMON_PORT)

subprocess.run(
    rsync_argv('-aX', '--keep-dirlinks', f'{src}/', f'{url}mod/'),
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

if getx(victim) == 'ORIGINAL':
    test_fail(
        "the daemon refused an xattr write on an excluded leaf that stock rsync "
        f"(3.2.7) performs: {victim} marker is still 'ORIGINAL' (expected "
        "'ATTACKER').  The exclude is name-based ('blink2/blocked', not the "
        "physical 'pub/blocked'); it must not block this.")
print("daemon exclude is name-based: an xattr write on an excluded leaf via an "
      "in-tree symlink is performed (3.2.7-equivalent)")

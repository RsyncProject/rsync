#!/usr/bin/env python3

import os
from harness.daemon_config import write_daemon_conf
from harness.rsync import (
    SCRATCHDIR, forced_protocol, rmtree, run_rsync, start_test_daemon, test_fail,
    test_skipped, xattr_get, xattr_set, xattrs_supported,
)
from harness import metadata

metadata(features={'protocol_30', 'xattr_runtime'}, transports={'pipe', 'tcp'})

if not xattrs_supported():
    test_skipped("user xattrs not available", capability='xattr_runtime')

proto = forced_protocol()
if proto is not None and proto < 30:
    test_skipped(f"xattr (-X) transfer requires protocol 30+ (negotiated {proto})",
                 capability='protocol_30')

DAEMON_PORT = 12992
XNAME = 'rsync.marker'

base = SCRATCHDIR / 'symlink-exclude-xattr'
rmtree(base)
base.mkdir()

mod = base / 'mod'
mod.mkdir()
pub = mod / 'pub'
pub.mkdir()
victim = pub / 'blocked'
victim.write_text("DATA\n")
xattr_set(XNAME, 'ORIGINAL', victim)
os.symlink('pub/', mod / 'blink2')

src = base / 'src'
(src / 'blink2').mkdir(parents=True)
sf = src / 'blink2' / 'blocked'
sf.write_text("DATA\n")
xattr_set(XNAME, 'ATTACKER', sf)

conf = write_daemon_conf(
    [('mod', {'path': str(mod), 'read only': 'no', 'exclude': '/pub/blocked'})])
url = start_test_daemon(conf, DAEMON_PORT)

# Kept links can reject their own xattrs while child xattrs still transfer
result = run_rsync('-aX', '--keep-dirlinks', f'{src}/', f'{url}mod/', check=False)
if result.returncode not in (0, 23):
    test_fail(f'xattr transfer exited {result.returncode}')

marker = xattr_get(XNAME, victim)
if marker != b'ATTACKER':
    test_fail(f'name-based exclusion prevented the in-tree xattr update: {marker!r}')

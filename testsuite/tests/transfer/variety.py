#!/usr/bin/env python3

import os
import re
import shlex
import subprocess
import sys

from harness.daemon_config import write_daemon_conf
from harness.rsync import (
    RSYNC, RSYNC_PEER, SCRATCHDIR, USE_TCP, acls_supported, compare_trees, devices_supported,
    make_variety_tree, owners_supported, rmtree, rsh_cmd, split_rsync_cmd, start_test_daemon,
    test_fail, xattrs_supported,
)
from harness import metadata

metadata(features={'daemon', 'filesystem-specials', 'remote-shell'}, transports={'pipe', 'tcp'}, min_peer='2.6.0', cost='expensive', mutates={'filesystem', 'process', 'socket'}, tags={'compatibility', 'metadata', 'transfer', 'version-mix'})

SSH = rsh_cmd()
DAEMON_PORT_OLD = 12931
DAEMON_PORT_NEW = 12932

def _peer_has(feature):
    try:
        vv = subprocess.run(split_rsync_cmd(RSYNC_PEER) + ['-VV'],
                            capture_output=True, text=True)
        return f'"{feature}": true' in vv.stdout
    except OSError:
        return False

def _forced_proto(cmd):
    m = re.search(r'--protocol[ =](\d+)', cmd)
    return int(m.group(1)) if m else None

_pinned = [p for p in (_forced_proto(RSYNC), _forced_proto(RSYNC_PEER)) if p]
_AX_PROTO_OK = not _pinned or min(_pinned) >= 30

WITH_X = xattrs_supported() and _peer_has('xattrs') and _AX_PROTO_OK
WITH_A = acls_supported() and _peer_has('ACLs') and _AX_PROTO_OK
WITH_DEV = devices_supported()
WITH_OWN = owners_supported()

os.chdir(SCRATCHDIR)

SRC = SCRATCHDIR / 'variety-src'
info = make_variety_tree(SRC, with_acls=WITH_A, with_xattrs=WITH_X,
                         with_devices=WITH_DEV, with_owners=WITH_OWN)
TR = info['transfer_root']
SRC_SLASH = f'{TR}/'
print(f"variety source: {sum(info['counts'].values())} entries "
      f"{info['counts']} (xattrs={WITH_X} acls={WITH_A} devices={WITH_DEV} "
      f"owners={WITH_OWN})")

DESTBASE = SCRATCHDIR / 'dest'
DESTBASE.mkdir(exist_ok=True)
VDST_BASE = SCRATCHDIR / 'vdst'
VDST_BASE.mkdir(exist_ok=True)

OK_CODES = (0, 23, 24)

_full = ['-a', '-H'] + (['-A'] if WITH_A else []) + (['-X'] if WITH_X else [])
PROFILES = {
    'P1-full':       _full + ['--specials', '--devices'],
    'P2-links':      ['-a', '--links'],
    'P3-copyunsafe': ['-a', '--copy-unsafe-links'],
    'P4-safelinks':  ['-a', '--safe-links'],
    'P5-noimplied':  _full + ['-D', '--no-implied-dirs'],
}
WIRE_PROFILES = ('P1-full', 'P2-links', 'P5-noimplied')
PAIRINGS = (('old', 'old'), ('new', 'old'), ('old', 'new'), ('new', 'new'))

results = []

def binof(role):
    return RSYNC_PEER if role == 'old' else RSYNC

def run(binary_cmd, args, label):
    argv = shlex.split(binary_cmd) + args
    proc = subprocess.run(argv, capture_output=True, text=True)
    if proc.returncode not in OK_CODES:
        sys.stdout.write(proc.stdout)
        sys.stderr.write(proc.stderr)
        test_fail(f"{label}: rsync exited {proc.returncode}: {' '.join(argv)}")

def newdir(base, name):
    d = base / name
    rmtree(d)
    d.mkdir(parents=True)
    return d

def compare(dest, ref, label):
    diffs = compare_trees(dest, ref, label,
                          with_acls=WITH_A, with_xattrs=WITH_X)
    if diffs:
        results.append((label, diffs))

def _make_conf(role):
    return write_daemon_conf([
        ('vsrc', {'path': str(TR), 'read only': 'yes'}),
        ('vdst', {'path': str(VDST_BASE), 'read only': 'no'}),
    ], global_options={
        'munge symlinks': 'no',
        'pid file': str(SCRATCHDIR / f'rsyncd-{role}.pid'),
        'log file': str(SCRATCHDIR / f'rsyncd-{role}.log'),
    }, name=f'vd-{role}.conf')

_conf_for = {'old': _make_conf('old'), 'new': _make_conf('new')}
_daemon_url = {}

def daemon_url(drole):
    if USE_TCP:
        if drole not in _daemon_url:
            port = DAEMON_PORT_OLD if drole == 'old' else DAEMON_PORT_NEW
            _daemon_url[drole] = start_test_daemon(_conf_for[drole], port,
                                                   rsync_cmd=binof(drole))
        return _daemon_url[drole]
    return start_test_daemon(_conf_for[drole], 0, rsync_cmd=binof(drole))

def do_transfer(transport, direction, crole, srole, prof, name):
    args = PROFILES[prof]
    if transport == 'local':
        d = newdir(DESTBASE, name)
        run(binof(crole), args + [SRC_SLASH, f'{d}/'], name)
        return d
    if transport == 'remote':
        d = newdir(DESTBASE, name)
        common = args + ['-e', SSH, f'--rsync-path={binof(srole)}']
        if direction == 'push':
            run(binof(crole), common + [SRC_SLASH, f'localhost:{d}/'], name)
        else:
            run(binof(crole), common + [f'localhost:{SRC_SLASH}', f'{d}/'], name)
        return d
    url = daemon_url(srole)
    if direction == 'push':
        d = newdir(VDST_BASE, name)
        run(binof(crole), args + [SRC_SLASH, f'{url}vdst/{name}/'], name)
        return d
    d = newdir(DESTBASE, name)
    run(binof(crole), args + [f'{url}vsrc/', f'{d}/'], name)
    return d

def run_scenario(transport, direction, prof, pairings):
    ref = None
    for crole, srole in pairings:
        tag = f"{transport}-{direction}-c{crole}-s{srole}/{prof}"
        dest = do_transfer(transport, direction, crole, srole, prof,
                           tag.replace('/', '_'))
        if ref is None:
            ref = dest
        else:
            compare(dest, ref, tag)

for prof in PROFILES:
    run_scenario('local', 'push', prof, (('old', 'old'), ('new', 'new')))

for prof in WIRE_PROFILES:
    for direction in ('push', 'pull'):
        run_scenario('remote', direction, prof, PAIRINGS)
        run_scenario('daemon', direction, prof, PAIRINGS)

if results:
    msg = [f"variety: {len(results)} case(s) diverged from the all-old "
           f"reference (peer={RSYNC_PEER!r}):"]
    for label, diffs in results:
        msg.append(f"\n========== {label} ==========")
        msg.extend(diffs)
    test_fail('\n'.join(msg))

print("variety: OK -- current rsync reproduces the all-old result on every "
      "local/remote/daemon scenario and new/old role pairing")

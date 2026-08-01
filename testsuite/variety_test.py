#!/usr/bin/env python3
# Differential regression test: introducing the current rsync on EITHER side of
# a transfer must not change the destination tree that an all-old (e.g. 3.2.7)
# transfer would produce, for a "variety tree" exercising every inode type rsync
# handles -- directories, regular files, symlinks (heavy coverage: to each type,
# escaping ../.. links, absolute links, links transiting outside the tree),
# fifos, sockets, char/block devices -- with varied permissions, xattrs, ACLs,
# hard links and (as root) mixed ownership. See make_variety_tree.
#
# Peer-agnostic: RSYNC is the current binary, RSYNC_PEER the --rsync-bin2 peer.
# With no peer (RSYNC_PEER == RSYNC) it degrades to a self-consistency check.
# Enabled first for the 3.2.7 peer via testsuite/expect/rsync_3.2.7.expect.
#
# ORACLE: for each (transport, direction, option-profile) the ALL-OLD pairing is
# the reference ("what 3.2.7 does"); the new->old, old->new and new->new pairings
# must all reproduce it byte-for-byte (tls listing + contents + xattrs + ACLs +
# hard-link grouping). A per-scenario reference is essential because some
# transports legitimately transform the tree (a daemon receiver sanitizes
# absolute/escaping symlinks) -- that transform is version-independent, so it
# must be compared against the same transport, not against a local copy.
#
# The symlinked-PATH-COMPONENT traversal cases (where the current build's
# hardened resolver intentionally diverges from 3.2.7) live in the separate
# variety-symlink-traversal_test.py so they can be xfailed without masking this.

import os
import re
import shlex
import subprocess
import sys

from rsyncfns import (
    RSYNC, RSYNC_PEER, SRCDIR, SCRATCHDIR, USE_TCP,
    make_variety_tree, compare_trees, rmtree,
    xattrs_supported, acls_supported, devices_supported, owners_supported,
    write_daemon_conf, start_test_daemon,
    test_fail, split_rsync_cmd, rsh_cmd,
)

SSH = rsh_cmd()
DAEMON_PORT_OLD = 12931
DAEMON_PORT_NEW = 12932

def _peer_has(feature):
    """True if the peer binary advertises `feature` ('xattrs'/'ACLs') in -VV.
    Pre-3.2.0 peers have no -VV, so this is False -- we then skip -X/-A for
    them, which is correct (we can't compare a feature one side can't carry)."""
    try:
        vv = subprocess.run(split_rsync_cmd(RSYNC_PEER) + ['-VV'],
                            capture_output=True, text=True)
        return f'"{feature}": true' in vv.stdout
    except OSError:
        return False


def _forced_proto(cmd):
    """The --protocol=N pinned on a binary's command string, else None (used by
    the fleet's check29/check30 passes)."""
    m = re.search(r'--protocol[ =](\d+)', cmd)
    return int(m.group(1)) if m else None


# ACLs and xattrs require protocol 30+; a forced --protocol=29 (the fleet's
# check29 pass) negotiates 29 and rsync aborts on -A/-X, so drop them then.
_pinned = [p for p in (_forced_proto(RSYNC), _forced_proto(RSYNC_PEER)) if p]
_AX_PROTO_OK = not _pinned or min(_pinned) >= 30

# Use a feature only when BOTH the current binary and the peer support it (and
# the negotiated protocol can carry it).
WITH_X = xattrs_supported() and _peer_has('xattrs') and _AX_PROTO_OK
WITH_A = acls_supported() and _peer_has('ACLs') and _AX_PROTO_OK
WITH_DEV = devices_supported()
WITH_OWN = owners_supported()

# Daemon/remote paths resolve against HOME (-> scratch dir); cd there.
os.chdir(SCRATCHDIR)

# One read-only source for the whole run, so every case compares copies of the
# exact same bytes. We transfer transfer_root/; the escape/ links point up into
# the (untransferred) above/ siblings, so on the dest they are copied as link
# strings -- identical for both binaries.
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

OK_CODES = (0, 23, 24)   # specials without -D -> rsync warns and returns 23/24

# -A/-X only when supported (3.2.7 has both). -L/--copy-links is excluded: the
# absolute/escape links point into the SOURCE scratch, so dereferencing tests
# source layout, not transfer fidelity.
_full = ['-a', '-H'] + (['-A'] if WITH_A else []) + (['-X'] if WITH_X else [])
PROFILES = {
    'P1-full':       _full + ['--specials', '--devices'],
    'P2-links':      ['-a', '--links'],
    'P3-copyunsafe': ['-a', '--copy-unsafe-links'],
    'P4-safelinks':  ['-a', '--safe-links'],
    'P5-noimplied':  _full + ['-D', '--no-implied-dirs'],
}
WIRE_PROFILES = ('P1-full', 'P2-links', 'P5-noimplied')   # over the wire
PAIRINGS = (('old', 'old'), ('new', 'old'), ('old', 'new'), ('new', 'new'))

results = []   # (case_label, [diff, ...])


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


# --- daemon plumbing: a config + daemon per role on demand ---
# munge symlinks defaults ON for a module (prepends /rsyncd-munged/); disable so
# the only daemon symlink transform is the standard absolute-link sanitisation,
# which both binaries do identically. Each role gets its own pid/log file so the
# old and new daemons can run concurrently in --use-tcp mode without a pid-lock
# clash.
def _make_conf(role):
    return write_daemon_conf([
        ('vsrc', {'path': str(TR), 'read only': 'yes'}),
        ('vdst', {'path': str(VDST_BASE), 'read only': 'no'}),
    ], globals={
        'munge symlinks': 'no',
        'pid file': str(SCRATCHDIR / f'rsyncd-{role}.pid'),
        'log file': str(SCRATCHDIR / f'rsyncd-{role}.log'),
    }, name=f'vd-{role}.conf')


_conf_for = {'old': _make_conf('old'), 'new': _make_conf('new')}
_daemon_url = {}


def daemon_url(drole):
    """URL for a daemon running `drole`'s binary. In --use-tcp mode each role
    gets its own listener (cached); in pipe mode this resets RSYNC_CONNECT_PROG
    on every call, so call it immediately before the client run."""
    if USE_TCP:
        if drole not in _daemon_url:
            port = DAEMON_PORT_OLD if drole == 'old' else DAEMON_PORT_NEW
            _daemon_url[drole] = start_test_daemon(_conf_for[drole], port,
                                                   rsync_cmd=binof(drole))
        return _daemon_url[drole]
    return start_test_daemon(_conf_for[drole], 0, rsync_cmd=binof(drole))


def do_transfer(transport, direction, crole, srole, prof, name):
    """Run one pairing and return the destination dir to compare."""
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
    # daemon
    url = daemon_url(srole)
    if direction == 'push':
        d = newdir(VDST_BASE, name)
        run(binof(crole), args + [SRC_SLASH, f'{url}vdst/{name}/'], name)
        return d
    d = newdir(DESTBASE, name)
    run(binof(crole), args + [f'{url}vsrc/', f'{d}/'], name)
    return d


def run_scenario(transport, direction, prof, pairings):
    """Run all `pairings` for one scenario; compare each non-ref to the all-old
    reference (pairings[0] must be the all-old pair)."""
    ref = None
    for crole, srole in pairings:
        tag = f"{transport}-{direction}-c{crole}-s{srole}/{prof}"
        dest = do_transfer(transport, direction, crole, srole, prof,
                           tag.replace('/', '_'))
        if ref is None:
            ref = dest          # all-old reference
        else:
            compare(dest, ref, tag)


# Local: single binary per run -> just old (ref) vs new, every profile.
for prof in PROFILES:
    run_scenario('local', 'push', prof, (('old', 'old'), ('new', 'new')))

# Remote-shell and daemon: all four client/server pairings, both directions.
for prof in WIRE_PROFILES:
    for direction in ('push', 'pull'):
        run_scenario('remote', direction, prof, PAIRINGS)
        run_scenario('daemon', direction, prof, PAIRINGS)


# --- verdict ---
if results:
    msg = [f"variety: {len(results)} case(s) diverged from the all-old "
           f"reference (peer={RSYNC_PEER!r}):"]
    for label, diffs in results:
        msg.append(f"\n========== {label} ==========")
        msg.extend(diffs)
    test_fail('\n'.join(msg))

print("variety: OK -- current rsync reproduces the all-old result on every "
      "local/remote/daemon scenario and new/old role pairing")

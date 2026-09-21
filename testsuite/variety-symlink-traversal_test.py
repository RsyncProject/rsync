#!/usr/bin/env python3
# Companion to variety_test.py for the cases the user specifically called out:
# the rsync CLIENT is started with a path that TRAVERSES a symlinked directory
# component -- as sender (the source path) and as receiver (the destination
# path), both as a trailing-slash symlink transfer-root and as an intermediate
# path component, with and without --keep-dirlinks.
#
# Empirically the current build (with its hardened per-component O_NOFOLLOW path
# resolver from the CVE-2026-29518 work) reproduces 3.2.7's result on all of
# these STATIC traversal patterns: a pre-existing symlinked directory component
# is followed by both, identically. The hardening only changes behaviour in the
# TOCTOU race scenario (a component swapped to a symlink mid-transfer), which is
# covered by the suite's dedicated symlink-race tests, not here. So this is a
# pass test: any future divergence on a legitimate traversal pattern is a
# regression (or an intended change to record as xfail in the peer manifest).
#
# Compares new-client vs old-client for each flavour and fails on any
# divergence (different exit code or different resulting tree).

import os
import shlex
import subprocess
import sys

from rsyncfns import (
    RSYNC, RSYNC_PEER, SRCDIR, SCRATCHDIR,
    make_variety_tree, compare_trees, rmtree,
    xattrs_supported, acls_supported, devices_supported, owners_supported,
    test_skipped, test_fail, split_rsync_cmd, rsh_cmd,
)

SSH = rsh_cmd()
WITH_X = xattrs_supported()
WITH_A = acls_supported()

os.chdir(SCRATCHDIR)

# A modest variety tree as the real source (depth 3 keeps it quick; symlink/perm
# coverage still present).
SRC = SCRATCHDIR / 'trav-src'
info = make_variety_tree(SRC, depth=3, with_acls=WITH_A, with_xattrs=WITH_X,
                         with_devices=devices_supported(),
                         with_owners=owners_supported())
TR = info['transfer_root']

WS = SCRATCHDIR / 'trav'
rmtree(WS)
WS.mkdir()

findings = []   # (case, description)


def run2(binary_cmd, args):
    """Run rsync without failing on nonzero (old and new may legitimately
    differ in success). Returns (returncode, combined_output)."""
    proc = subprocess.run(shlex.split(binary_cmd) + args,
                          capture_output=True, text=True)
    return proc.returncode, proc.stdout + proc.stderr


def diff_trees(a, b, label):
    return compare_trees(a, b, label, with_acls=WITH_A,
                         with_xattrs=WITH_X)


def observe(case, new_dest, old_dest, rc_new, rc_old, out_new, out_old):
    notes = []
    if rc_new != rc_old:
        notes.append(f"exit codes differ: new={rc_new} old={rc_old}")
    # With --relative the dest CONTAINER dir is not a copy of any source dir, so
    # rsync leaves its mtime at "now" (not preserved by either version); the new
    # and old runs stamp it at slightly different instants, which would flake the
    # listing across a 1-second boundary. It is not meaningful content, so pin
    # both compared roots to a common mtime before diffing.
    for d in (new_dest, old_dest):
        try:
            os.utime(d, (1000000000, 1000000000))
        except OSError:
            pass
    tdiffs = diff_trees(new_dest, old_dest, case)
    if tdiffs:
        notes.append("result trees differ:\n" + "\n".join(tdiffs[:8]))
    if notes:
        findings.append((case, "\n".join(notes)))


# --- Case A: sender transfer-root IS a symlink to a directory (trailing slash) ---
def case_sender_root():
    os.symlink(str(TR), WS / 'linkdir')
    out = {}
    for role, binc in (('new', RSYNC), ('old', RSYNC_PEER)):
        d = WS / f'A-{role}'
        rmtree(d); d.mkdir()
        rc, o = run2(binc, ['-a', f'{WS}/linkdir/', f'{d}/'])
        out[role] = (d, rc, o)
    observe('A-sender-root-symlink',
            out['new'][0], out['old'][0],
            out['new'][1], out['old'][1], out['new'][2], out['old'][2])


# --- Case B: sender path has an INTERMEDIATE symlinked component ---
def case_sender_intermediate():
    base = WS / 'B-base'
    (base / 'realcomp' / 'inner').mkdir(parents=True)
    # copy a couple of real files under inner
    (base / 'realcomp' / 'inner' / 'file1').write_text('hello-1\n')
    (base / 'realcomp' / 'inner' / 'file2').write_text('hello-2\n')
    os.symlink('realcomp', base / 'linkcomp')
    for relflag in ([], ['-R']):       # plain and --relative (implied dirs)
        tag = 'B-sender-intermediate' + ('-R' if relflag else '')
        out = {}
        for role, binc in (('new', RSYNC), ('old', RSYNC_PEER)):
            d = WS / f'{tag}-{role}'
            rmtree(d); d.mkdir()
            rc, o = run2(binc, ['-a'] + relflag
                         + [f'{base}/linkcomp/inner/', f'{d}/'])
            out[role] = (d, rc, o)
        observe(tag, out['new'][0], out['old'][0],
                out['new'][1], out['old'][1], out['new'][2], out['old'][2])


# --- Case C: receiver destination IS a symlink to a directory ---
def case_receiver_root():
    out = {}
    for role, binc in (('new', RSYNC), ('old', RSYNC_PEER)):
        real = WS / f'C-real-{role}'
        real.mkdir()
        link = WS / f'C-link-{role}'
        os.symlink(str(real), link)
        rc, o = run2(binc, ['-a', '--keep-dirlinks', f'{TR}/', f'{link}/'])
        out[role] = (real, rc, o)
    observe('C-receiver-root-symlink', out['new'][0], out['old'][0],
            out['new'][1], out['old'][1], out['new'][2], out['old'][2])


# --- Case D: receiver path has an INTERMEDIATE symlinked component, with and
#     without --keep-dirlinks (the pointed "create through a symlinked dest
#     component" case) ---
def case_receiver_intermediate():
    for kdl in ([], ['--keep-dirlinks']):
        tag = 'D-receiver-intermediate' + ('-kdl' if kdl else '')
        out = {}
        for role, binc in (('new', RSYNC), ('old', RSYNC_PEER)):
            base = WS / f'{tag}-base-{role}'
            (base / 'realcomp').mkdir(parents=True)
            os.symlink('realcomp', base / 'linkcomp')
            rc, o = run2(binc, ['-a'] + kdl
                         + [f'{TR}/', f'{base}/linkcomp/inner/'])
            landing = base / 'realcomp' / 'inner'
            if not landing.exists():
                landing.mkdir(parents=True, exist_ok=True)
            out[role] = (landing, rc, o)
        observe(tag, out['new'][0], out['old'][0],
                out['new'][1], out['old'][1], out['new'][2], out['old'][2])


if split_rsync_cmd(RSYNC) == split_rsync_cmd(RSYNC_PEER):
    test_skipped("no old peer selected (RSYNC_PEER == RSYNC); nothing to "
                 "compare for symlink-traversal divergence")

case_sender_root()
case_sender_intermediate()
case_receiver_root()
case_receiver_intermediate()

if findings:
    msg = [f"variety-symlink-traversal: {len(findings)} traversal case(s) "
           f"diverge between current and peer={RSYNC_PEER!r}:"]
    for case, desc in findings:
        msg.append(f"\n========== {case} ==========")
        msg.append(desc)
    test_fail("\n".join(msg))

print("variety-symlink-traversal: current and peer agree on all traversal cases")

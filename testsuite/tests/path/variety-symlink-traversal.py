#!/usr/bin/env python3

import os
import shlex
import subprocess

from harness.rsync import (
    RSYNC,
    RSYNC_PEER,
    SCRATCHDIR,
    make_variety_tree,
    compare_trees,
    rmtree,
    xattrs_supported,
    acls_supported,
    devices_supported,
    owners_supported,
    test_skipped,
    test_fail,
    split_rsync_cmd,
    rsh_cmd,
)
from harness import metadata

metadata(features={'remote-shell', 'symlink'}, transports={'pipe', 'tcp'}, min_peer='2.6.0', cost='expensive', mutates={'filesystem', 'process'}, tags={'compatibility', 'security', 'transfer', 'version-mix'})

SSH = rsh_cmd()
WITH_X = xattrs_supported()
WITH_A = acls_supported()

os.chdir(SCRATCHDIR)

SRC = SCRATCHDIR / 'trav-src'
info = make_variety_tree(SRC, depth=3, with_acls=WITH_A, with_xattrs=WITH_X,
                         with_devices=devices_supported(),
                         with_owners=owners_supported())
TR = info['transfer_root']

WS = SCRATCHDIR / 'trav'
rmtree(WS)
WS.mkdir()

findings = []

def run2(binary_cmd, args):
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

def case_sender_root():
    os.symlink(str(TR), WS / 'linkdir')
    out = {}
    for role, binc in (('new', RSYNC), ('old', RSYNC_PEER)):
        d = WS / f'A-{role}'
        rmtree(d)
        d.mkdir()
        rc, o = run2(binc, ['-a', f'{WS}/linkdir/', f'{d}/'])
        out[role] = (d, rc, o)
    observe('A-sender-root-symlink',
            out['new'][0], out['old'][0],
            out['new'][1], out['old'][1], out['new'][2], out['old'][2])

def case_sender_intermediate():
    base = WS / 'B-base'
    (base / 'realcomp' / 'inner').mkdir(parents=True)
    (base / 'realcomp' / 'inner' / 'file1').write_text('hello-1\n')
    (base / 'realcomp' / 'inner' / 'file2').write_text('hello-2\n')
    os.symlink('realcomp', base / 'linkcomp')
    for relflag in ([], ['-R']):
        tag = 'B-sender-intermediate' + ('-R' if relflag else '')
        out = {}
        for role, binc in (('new', RSYNC), ('old', RSYNC_PEER)):
            d = WS / f'{tag}-{role}'
            rmtree(d)
            d.mkdir()
            rc, o = run2(binc, ['-a'] + relflag
                         + [f'{base}/linkcomp/inner/', f'{d}/'])
            out[role] = (d, rc, o)
        observe(tag, out['new'][0], out['old'][0],
                out['new'][1], out['old'][1], out['new'][2], out['old'][2])

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
                 "compare for symlink-traversal divergence", capability='legacy_peer')

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

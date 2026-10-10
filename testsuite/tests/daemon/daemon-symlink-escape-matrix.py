#!/usr/bin/env python3

import os
import subprocess
from pathlib import Path

from harness.mutation import find_attacker_uid
from harness.rsync import (
    RSYNC, RSYNC_PEER, SCRATCHDIR, require_tcp, rmtree, rsync_argv, rsync_supports,
    start_test_daemon, test_fail, test_skipped, write_daemon_conf,
)

PORT_CUR = 12909
PORT_ORACLE = 12910
SECRET = "TARGET-CONTENT\n"
PWNED = "WROTE-THROUGH-LINK\n"

require_tcp("the daemon symlink-resolution flow needs a real TCP peer")
if os.geteuid() != 0:
    test_skipped("requires root to plant a foreign-owned symlink and serve as root",
                 capability='root')
if not rsync_supports('--copy-dirlinks'):
    test_skipped("rsync lacks --copy-dirlinks")
ATT = find_attacker_uid()
if ATT is None:
    test_skipped("no untrusted-uid user available for the cross-uid plant",
                 capability='cross_uid')

_repo = Path(os.environ['srcdir'])
ORACLE_BIN = None
if RSYNC_PEER != RSYNC:
    ORACLE_BIN = RSYNC_PEER
elif (_repo / 'old_versions' / 'rsync_3.2.7').is_file():
    ORACLE_BIN = str(_repo / 'old_versions' / 'rsync_3.2.7')

if ORACLE_BIN:
    try:
        _probe = subprocess.run([ORACLE_BIN, '--version'],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
        if _probe.returncode != 0:
            ORACLE_BIN = None
    except (OSError, subprocess.TimeoutExpired):
        ORACLE_BIN = None

TYPES = ('rel-within', 'rel-outside', 'rel-transits', 'abs-outside', 'abs-inside')
OUTSIDE = {'rel-outside', 'abs-outside'}
VECTORS = ('read', 'write', 'read-plain', 'write-plain', 'compare-dest')

base = SCRATCHDIR / 'symlink-escape-matrix'
rmtree(base)
base.mkdir(parents=True)

MODS = {}
mod_conf = []
for insecure in (False, True):
    for munge in (False, True):
        name = f"m_{'ins' if insecure else 'safe'}_{'munge' if munge else 'nomunge'}"
        moddir = base / (name + '_root')
        outside = base / (name + '_outside')
        moddir.mkdir()
        outside.mkdir()
        MODS[(insecure, munge)] = (name, moddir, outside)
        mod_conf.append((name, {
            'path': str(moddir), 'use chroot': 'no', 'read only': 'no',
            'uid': '0', 'gid': '0',
            'insecure links': 'yes' if insecure else 'no',
            'munge symlinks': 'yes' if munge else 'no',
        }))

conf = write_daemon_conf(mod_conf, name='symlink-escape-matrix.conf')
url_cur = start_test_daemon(conf, PORT_CUR, rsync_cmd=RSYNC)
url_oracle = None
if ORACLE_BIN:
    conf_oracle = write_daemon_conf(
        mod_conf,
        {'pid file': str(SCRATCHDIR / 'rsyncd-oracle.pid'),
         'log file': str(SCRATCHDIR / 'rsyncd-oracle.log')},
        name='symlink-escape-matrix-oracle.conf')
    url_oracle = start_test_daemon(conf_oracle, PORT_ORACLE, rsync_cmd=ORACLE_BIN)

def link_target(moddir, outside, sltype):
    realdir = moddir / 'realdir'
    if sltype == 'rel-within':
        return 'realdir', realdir, False
    if sltype == 'rel-outside':
        return '../' + outside.name, outside, True
    if sltype == 'rel-transits':
        return f'../{moddir.name}/realdir', realdir, False
    if sltype == 'abs-outside':
        return str(outside), outside, True
    if sltype == 'abs-inside':
        return str(realdir), realdir, False
    raise AssertionError(sltype)

def attempt(url, insecure, munge, origin, vector, sltype):
    name, moddir, outside = MODS[(insecure, munge)]
    rmtree(moddir)
    moddir.mkdir()
    rmtree(outside)
    outside.mkdir()
    realdir = moddir / 'realdir'
    realdir.mkdir()
    (realdir / 'tgtfile').write_text(SECRET)
    (outside / 'tgtfile').write_text(SECRET)
    symval, resolved, t_out = link_target(moddir, outside, sltype)
    evil = moddir / 'evil'

    if origin == 'preexist':
        os.symlink(symval, evil)
        os.lchown(evil, ATT, ATT)
    else:
        up = base / 'srcup'
        rmtree(up)
        up.mkdir()
        os.symlink(symval, up / 'evil')
        subprocess.run(rsync_argv('-al', f'{up}/', f'{url}{name}/'),
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if not evil.is_symlink():
            return False, t_out

    def run(*args):
        subprocess.run(rsync_argv(*args),
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    if vector == 'read':
        dest = base / 'dest'
        rmtree(dest)
        dest.mkdir()
        run('-r', '--copy-dirlinks', f'{url}{name}/', f'{dest}/')
        through = dest / 'evil' / 'tgtfile'
        return (through.is_file() and through.read_text() == SECRET), t_out

    if vector == 'write':
        sw = base / 'srcw'
        rmtree(sw)
        sw.mkdir()
        (sw / 'evil').mkdir()
        (sw / 'evil' / 'pwned').write_text(PWNED)
        run('-r', '--keep-dirlinks', f'{sw}/', f'{url}{name}/')
        landed = resolved / 'pwned'
        return (landed.is_file() and landed.read_text() == PWNED), t_out

    if vector == 'read-plain':
        dest = base / 'dest'
        rmtree(dest)
        dest.mkdir()
        run('-r', f'{url}{name}/evil/', f'{dest}/')
        through = dest / 'tgtfile'
        return (through.is_file() and through.read_text() == SECRET), t_out

    if vector == 'write-plain':
        sw = base / 'srcw'
        rmtree(sw)
        sw.mkdir()
        (sw / 'pwned').write_text(PWNED)
        run('-r', f'{sw}/', f'{url}{name}/evil/')
        landed = resolved / 'pwned'
        return (landed.is_file() and landed.read_text() == PWNED), t_out

    sc = base / 'srcc'
    rmtree(sc)
    sc.mkdir()
    (sc / 'tgtfile').write_text(SECRET)
    run('-r', '--checksum', '--compare-dest=/evil', f'{sc}/', f'{url}{name}/')
    pushed = moddir / 'tgtfile'
    return (not pushed.is_file()), t_out

def static_followed(insecure, munge, origin, sltype):
    if origin == 'uploaded':
        return sltype == 'rel-within' and not munge
    return True

_REPORT = os.environ.get('REPORT_MATRIX')
grid, mismatches, escapes = [], [], []
for insecure in (False, True):
    for munge in (False, True):
        for origin in ('preexist', 'uploaded'):
            for vector in VECTORS:
                for sltype in TYPES:
                    got, t_out = attempt(url_cur, insecure, munge, origin, vector, sltype)
                    esc = got and t_out

                    if not insecure:
                        line = (f"ins=0 munge={int(munge)} {origin:8} {vector:12} "
                                f"{sltype:11}: followed={int(got)} "
                                f"{'ESCAPE' if esc else ''}")
                        if esc:
                            escapes.append(f"DEFAULT ESCAPE: munge={int(munge)} "
                                           f"{origin}/{vector}/{sltype}")
                    else:
                        if url_oracle is not None:
                            want, src = attempt(url_oracle, insecure, munge,
                                                origin, vector, sltype)[0], '327'
                        else:
                            want, src = static_followed(insecure, munge, origin, sltype), 'contract'
                        line = (f"ins=1 munge={int(munge)} {origin:8} {vector:12} "
                                f"{sltype:11}: followed={int(got)} want={int(want)}({src})")
                        if got != want:
                            mismatches.append(
                                f"insecure=yes munge={'yes' if munge else 'no'} "
                                f"{origin}/{vector}/{sltype}: followed={got}, "
                                f"{src} expected={want}")
                    grid.append(line)

if _REPORT:
    test_fail("REPORT-MATRIX grid:\n  " + "\n  ".join(grid))
problems = []
if escapes:
    problems.append("secure-default confinement FAILED (out-of-module access):\n  "
                    + "\n  ".join(escapes))
if mismatches:
    problems.append("insecure-links opt-out did NOT match stock 3.2.7:\n  "
                    + "\n  ".join(mismatches))
if problems:
    test_fail("\n".join(problems))

oracle_note = ("vs a real 3.2.7 daemon oracle" if url_oracle
               else "vs the static fallback contract (no 3.2.7 binary present)")
print(f'daemon symlink matrix stayed confined; insecure-links control: {oracle_note}')

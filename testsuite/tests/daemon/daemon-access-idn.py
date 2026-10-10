#!/usr/bin/env python3

import re
import subprocess

from harness.daemon import probe_module
from harness.rsync import (
    FROMDIR, SCRATCHDIR,
    claim_ports, make_tree, require_tcp, rmtree, rsync_argv, start_rsyncd,
    start_test_daemon, test_fail, test_skipped,
)

PROBE_PORT = 12896
DAEMON_PORT = 12898
require_tcp("hosts allow/deny hostname matching needs a real TCP peer")

if '"IDN": true' not in subprocess.run(rsync_argv('-VV'), capture_output=True,
                                       text=True).stdout:
    test_skipped("rsync built without IDN support", capability='idn')

src = FROMDIR
rmtree(src)
make_tree(src, depth=2)

def write_conf(path, modules, log, pidfile):
    lines = [
        f'pid file = {pidfile}',
        'use chroot = no',
        'forward lookup = no',
        f'log file = {log}',
        '',
    ]
    for mod, params in modules:
        lines.append(f'[{mod}]')
        lines.append(f'\tpath = {src}')
        lines.append('\tread only = yes')
        lines += [f'\t{k} = {v}' for k, v in params.items()]
        lines.append('')
    path.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return path

probe_log = SCRATCHDIR / 'rsyncd-idn-probe.log'
probe_conf = write_conf(SCRATCHDIR / 'access-idn-probe.conf', [('probe', {})],
                        probe_log, SCRATCHDIR / 'rsyncd-idn-probe.pid')
claim_ports(PROBE_PORT)
probe = start_rsyncd(probe_conf, PROBE_PORT)
try:
    subprocess.run(rsync_argv('-r', f'rsync://localhost:{PROBE_PORT}/probe/'),
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
finally:
    probe.terminate()
    probe.wait(timeout=5)

m = re.search(r'connect from (\S+) \(', probe_log.read_text(errors='replace'))
if not m:
    test_fail(f"no 'connect from' line in the probe daemon log {probe_log}")
peer = m.group(1)
print(f"daemon sees its peer as {peer!r}")

def fullwidth(name):
    return ''.join(chr(ord(c) - 0x21 + 0xFF01) if '!' <= c <= '~' and c != '.'
                   else c for c in name)

if fullwidth(peer) == peer:
    test_skipped(f"peer name {peer!r} has no ASCII to respell in fullwidth")

ZWSP = '​'
FW_STAR = '＊'
FW_SLASH = '／'

modules = [
    ('ascii-name',    {'hosts allow': peer}),
    ('ascii-upper',   {'hosts allow': peer.upper()}),
    ('ascii-wild',    {'hosts allow': peer[:1] + '*'}),
    ('idn-name',      {'hosts allow': fullwidth(peer)}),
    ('idn-mixedcase', {'hosts allow': fullwidth(peer.upper())}),
    ('idn-deny',      {'hosts deny': fullwidth(peer)}),
    ('idn-other',     {'hosts allow': 'čičku.example'}),
    ('idn-puny',      {'hosts allow': 'xn--iku-eqab.example'}),
    ('idn-nfd',       {'hosts allow': 'c\u030ci' 'c\u030cku.example'}),
    ('wide-star',     {'hosts allow': FW_STAR}),
    ('wide-star-dom', {'hosts allow': FW_STAR + '.example'}),
    ('wide-mask',     {'hosts allow': '127.0.0.0' + FW_SLASH + '8'}),
    ('bad-idn',       {'hosts allow': ZWSP + '.example'}),
]

conf = write_conf(SCRATCHDIR / 'access-idn.conf', modules,
                  SCRATCHDIR / 'rsyncd.log', SCRATCHDIR / 'rsyncd.pid')
url = start_test_daemon(conf, DAEMON_PORT)

def allowed(mod, why):
    if probe_module(url, mod) != 0:
        test_fail(f"connection to {mod} should be ALLOWED ({why}) but was refused")

def denied(mod, why):
    if probe_module(url, mod) == 0:
        test_fail(f"connection to {mod} should be DENIED ({why}) but succeeded")

allowed('ascii-name', "the peer's own name in a hosts allow")
allowed('ascii-upper', "hostname matching is case-insensitive")
allowed('ascii-wild', "an ASCII wildcard still matches")
allowed('idn-name', f"fullwidth {peer!r} folds to the peer's name")
allowed('idn-mixedcase', "IDNA case-folds the token")
denied('idn-deny', "hosts deny sees the folded token too")
denied('idn-other', "a different IDN must not match the peer")
denied('idn-puny', "an A-label for a different host must not match the peer")
denied('idn-nfd', "a decomposed spelling of that name must not match either")
denied('wide-star', "U+FF0A must not become a '*' that allows every host")
denied('wide-star-dom', "U+FF0A must not become a wildcard label")
denied('wide-mask', "U+FF0F must not become an address/mask separator")
denied('bad-idn', "an unconvertible IDN must not match anything")

print("daemon-access-idn: IDN hosts allow/deny matching + no wildcard widening")

#!/usr/bin/env python3
# Transfer checksums that require an explicit choice.
import json
import os
from random import Random
import re
import subprocess

from harness.environment import FROMDIR, TODIR
from harness.filesystem import assert_same, makepath, rmtree, walk_files
from harness.results import test_fail
from harness.rsync import forced_protocol, rsync_argv, run_rsync
from harness import metadata

metadata(transports={'pipe'}, mutates={'environment', 'filesystem'}, tags={'transfer', 'regression'})

ALGOS = ('sha3-256', 'sha3-384', 'sha3-512', 'sha256')

version = json.loads(run_rsync('-VV', capture_output=True).stdout)
choices = version['checksum_list']
available = [algo for algo in ALGOS if algo in choices]
if ('sha256' in choices) != ('sha256' in version['daemon_auth_list']):
    test_fail('SHA-256 transfer availability does not match the linked EVP digest')
os.environ.pop('RSYNC_CHECKSUM_LIST', None)
protocol = forced_protocol()
legacy_checksum = protocol is not None and protocol < 30

rmtree(FROMDIR)
makepath(FROMDIR / 'sub')
# Empty, small and chunk-spanning sizes (CHUNK_SIZE is 32k internally).
(FROMDIR / 'empty').write_bytes(b'')
(FROMDIR / 'small').write_text('hello checksum\n')
size = 70 * 1024
(FROMDIR / 'sub' / 'big').write_bytes(Random(0).getrandbits(size * 8).to_bytes(size, 'big'))

src, dst = f'{FROMDIR}/', f'{TODIR}/'


def check_tree(label):
    names = sorted(p.relative_to(FROMDIR) for p in walk_files(FROMDIR))
    if names != sorted(p.relative_to(TODIR) for p in walk_files(TODIR)):
        test_fail(f'{label}: file lists differ')
    for rel in names:
        assert_same(TODIR / rel, FROMDIR / rel, label=f'{label} {rel}')


def check_default_checksum():
    rmtree(TODIR)
    result = run_rsync('-a', '--debug=NSTR2', src, dst, capture_output=True, check=False)
    if result.returncode:
        test_fail(f'default checksum transfer failed: {result}')
    lines = result.stdout.splitlines()
    if legacy_checksum:
        if 'Client checksum: md4' not in lines:
            test_fail(f'protocol {protocol}: expected the MD4 default: {result.stdout!r}')
    else:
        for peer in ('Client', 'Server'):
            prefix = f'{peer} checksum list (on client): '
            offers = [line[len(prefix):].split() for line in lines if line.startswith(prefix)]
            if len(offers) != 1 or set(offers[0]).intersection(ALGOS):
                test_fail(f'{peer}: invalid default checksum offer: {offers!r}')
    check_tree('auto')


def transfer_with_checksum_list(choices):
    return subprocess.run(rsync_argv('-a', '--debug=NSTR2', src, dst),
                          env={**os.environ, 'RSYNC_CHECKSUM_LIST': choices},
                          capture_output=True, text=True)


def check_checksum_list(algo):
    rmtree(TODIR)
    result = transfer_with_checksum_list(algo)
    expected = f'Client negotiated checksum: {algo}'
    if legacy_checksum:
        # Without negotiation the list may allow MD4 or refuse it, not replace it.
        if result.returncode != 4 or 'Failed to negotiate a checksum choice.' not in result.stderr:
            test_fail(f'protocol {protocol}: expected a {algo}-only list to be refused: {result}')
        rmtree(TODIR)
        result = transfer_with_checksum_list(f'{algo} md4')
        expected = 'Client checksum: md4'
    if result.returncode or expected not in result.stdout.splitlines():
        test_fail(f'RSYNC_CHECKSUM_LIST: expected {expected!r}: {result}')
    check_tree('checksum list')


check_default_checksum()


for algo in ALGOS:
    # Whole-file path (transfer checksum only).
    rmtree(TODIR)
    if algo not in available:
        result = run_rsync('-a', f'--checksum-choice={algo}', src, dst,
                           capture_output=True, check=False)
        if result.returncode != 4 or f'unknown checksum name: {algo}' not in result.stderr:
            test_fail(f'{algo}: an unavailable digest was not refused: {result.returncode}: {result.stderr}')
        continue
    result = run_rsync('-a', f'--checksum-choice={algo}', '--debug=NSTR2', src, dst,
                       capture_output=True, check=False)
    if result.returncode or f'Client checksum: {algo}' not in result.stdout.splitlines():
        test_fail(f'explicit choice did not select {algo}: {result}')
    check_tree(algo)
    # Pre-transfer + transfer checksum path (-c forces file_checksum to run).
    rmtree(TODIR)
    run_rsync('-ac', f'--checksum-choice={algo}', src, dst)
    check_tree(f'{algo} -c')
    # --checksum-choice with an explicit xfer,file pair.
    rmtree(TODIR)
    run_rsync('-ac', f'--checksum-choice={algo},{algo}', src, dst)
    check_tree(f'{algo},{algo} -c')

for algo in available:
    check_checksum_list(algo)
    with (TODIR / 'sub' / 'big').open('r+b') as stream:
        stream.seek(32 * 1024)
        stream.write(b'modified locally\n')
    result = run_rsync('-ac', '--no-whole-file', '--stats', f'--checksum-choice={algo}',
                       src, dst, capture_output=True)
    for field in ('Matched', 'Literal'):
        count = re.search(rf'^{field} data: ([0-9,]+) bytes$', result.stdout, re.MULTILINE)
        if not count or int(count[1].replace(',', '')) == 0:
            test_fail(f'{algo} delta: no {field.lower()} data: {result.stdout!r}')
    check_tree(f'{algo} delta')

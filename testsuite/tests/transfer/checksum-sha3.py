#!/usr/bin/env python3
# SHA-3 checksum support (sha3-256, sha3-384, sha3-512).
#
# Needs rsync built with OpenSSL against a libcrypto that provides the SHA-3
# family (OpenSSL 1.1.1 or newer); without it the test reports the openssl
# capability as unsupported.
#
# Each algorithm is used for the whole-file transfer checksum, for the
# pre-transfer file checksum (-c), and as an explicit xfer,file pair; then a
# delta update of a modified destination is checked with sha3-256.

import json
import os

from harness.environment import FROMDIR, TODIR
from harness.filesystem import assert_same, makepath, rmtree, walk_files
from harness.results import test_fail, test_skipped
from harness.rsync import run_rsync
from harness import metadata

metadata(features={'openssl'}, transports={'pipe'})

ALGOS = ('sha3-256', 'sha3-384', 'sha3-512')

vv = json.loads(run_rsync('-VV', check=True, capture_output=True).stdout)
missing = [a for a in ALGOS if a not in vv.get('checksum_list', [])]
if missing:
    test_skipped(f"rsync was not built with OpenSSL SHA-3 support (missing: {', '.join(missing)})",
                 capability='openssl')

rmtree(FROMDIR)
makepath(FROMDIR / 'sub')
# Empty, small and chunk-spanning sizes (CHUNK_SIZE is 32k internally).
(FROMDIR / 'empty').write_bytes(b'')
(FROMDIR / 'small').write_text('hello sha-3\n')
(FROMDIR / 'sub' / 'big').write_bytes(os.urandom(70 * 1024))

src, dst = f'{FROMDIR}/', f'{TODIR}/'


def check_tree(label):
    names = sorted(p.relative_to(FROMDIR) for p in walk_files(FROMDIR))
    if names != sorted(p.relative_to(TODIR) for p in walk_files(TODIR)):
        test_fail(f'{label}: file lists differ')
    for rel in names:
        assert_same(TODIR / rel, FROMDIR / rel, label=f'{label} {rel}')


for algo in ALGOS:
    # Whole-file path (transfer checksum only).
    rmtree(TODIR)
    run_rsync('-a', f'--checksum-choice={algo}', src, dst)
    check_tree(algo)
    # Pre-transfer + transfer checksum path (-c forces file_checksum to run).
    rmtree(TODIR)
    run_rsync('-ac', f'--checksum-choice={algo}', src, dst)
    check_tree(f'{algo} -c')
    # --checksum-choice with an explicit xfer,file pair.
    rmtree(TODIR)
    run_rsync('-ac', f'--checksum-choice={algo},{algo}', src, dst)
    check_tree(f'{algo},{algo} -c')

# Delta-update path: change the destination, re-sync with SHA-3, confirm it matches again.
rmtree(TODIR)
run_rsync('-a', '--checksum-choice=sha3-256', src, dst)
with open(TODIR / 'small', 'a') as f:
    f.write('modified locally\n')
run_rsync('-a', '--no-whole-file', '--checksum-choice=sha3-256', src, dst)
check_tree('sha3-256 delta')
if (TODIR / 'small').read_text() != 'hello sha-3\n':
    test_fail('delta update with sha3-256 did not restore the source content')

print('checksum-sha3: sha3-256/384/512 transfer and file checksums, and a sha3-256 delta update')

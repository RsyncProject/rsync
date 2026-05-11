#!/usr/bin/env python3
# SHA-3 checksum support (sha3-256, sha3-384, sha3-512).
#
# Requires rsync to be built with OpenSSL support and linked against a
# libcrypto that exposes the SHA-3 family (OpenSSL 1.1.1 or newer). If the
# local rsync does not list SHA-3 (no OpenSSL at build time, or an older
# libcrypto), the test is skipped rather than failed.
#
# Each algorithm is used for the whole-file transfer checksum, for the
# pre-transfer file checksum (-c), and as an explicit xfer,file pair; then a
# delta update of a modified destination is checked with sha3-256.

import json
import os

from rsyncfns import (
    FROMDIR, TODIR,
    checkit, makepath, rmtree, run_rsync, test_fail, test_skipped,
)

ALGOS = ('sha3-256', 'sha3-384', 'sha3-512')

vv = json.loads(run_rsync('-VV', capture_output=True).stdout)
missing = [a for a in ALGOS if a not in vv.get('checksum_list', [])]
if missing:
    test_skipped(f"rsync was not built with OpenSSL SHA-3 support (missing: {', '.join(missing)})")

rmtree(FROMDIR)
makepath(FROMDIR / 'sub')
# Mix of empty, small, and chunk-spanning sizes (CHUNK_SIZE is 32k internally).
(FROMDIR / 'empty').write_bytes(b'')
(FROMDIR / 'small').write_text('hello sha-3\n')
(FROMDIR / 'sub' / 'big').write_bytes(os.urandom(70 * 1024))

src, dst = f'{FROMDIR}/', f'{TODIR}/'
for algo in ALGOS:
    # Whole-file path (transfer checksum only).
    rmtree(TODIR)
    checkit(['-a', f'--checksum-choice={algo}', src, dst], FROMDIR, TODIR)
    # Pre-transfer + transfer checksum path (-c forces file_checksum to run).
    rmtree(TODIR)
    checkit(['-ac', f'--checksum-choice={algo}', src, dst], FROMDIR, TODIR)
    # --checksum-choice with an explicit xfer,file pair.
    rmtree(TODIR)
    checkit(['-ac', f'--checksum-choice={algo},{algo}', src, dst], FROMDIR, TODIR)

# Delta-update path: mutate the destination, re-sync with SHA-3, confirm match.
rmtree(TODIR)
run_rsync('-a', '--checksum-choice=sha3-256', src, dst)
with open(TODIR / 'small', 'a') as f:
    f.write('modified locally\n')
checkit(['-a', '--no-whole-file', '--checksum-choice=sha3-256', src, dst], FROMDIR, TODIR)
if (TODIR / 'small').read_text() != 'hello sha-3\n':
    test_fail('delta update with sha3-256 did not restore the source content')

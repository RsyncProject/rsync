#!/usr/bin/env python3
"""Regression: a short transfer checksum must not over-state the block s2length.

A full-checksum (--append-verify redo) pass computes the strong block sum length
(s2length).  The generator used to cap it at SUM_LENGTH (16), the legacy MD4/MD5
digest size, regardless of the negotiated algorithm.  Since the sum2 array holds
xfer_sum_len-byte elements and the sender rejects an s2length larger than
xfer_sum_len, a sub-16-byte transfer checksum -- xxh64 (8 bytes), which is what
rsync negotiates when the build's libxxhash lacks xxh128/xxh3 (e.g. Ubuntu
20.04) -- made the sender die with "Invalid checksum length 16 [sender]"
(protocol incompatibility, code 2).

Forcing --checksum-choice=xxh64 reproduces it on any build that has xxhash, so
this guards the fix without needing an old-libxxhash host.  Skipped where xxh64
is unavailable (a build without xxhash).
"""

import json

from rsyncfns import (
    FROMDIR, TODIR, assert_same, make_data_file, rmtree, run_rsync,
    test_skipped,
)

vv = json.loads(run_rsync('-VV', check=True, capture_output=True).stdout)
if 'xxh64' not in vv.get('checksum_list', []):
    test_skipped("xxh64 not in this build's checksum list (no xxhash)")

src, dst = FROMDIR, TODIR
rmtree(src)
rmtree(dst)
src.mkdir(parents=True)
dst.mkdir(parents=True)

# Source longer than the destination so --append has bytes to add; the dest is a
# *corrupted* prefix so --append-verify's whole-file check fails and the file is
# redone with a full checksum -- the csum_length == SUM_LENGTH path that emitted
# the over-long s2length.
make_data_file(src / 'f', 40000)
full = (src / 'f').read_bytes()
prefix = bytearray(full[:20000])
prefix[0:64] = b'\x00' * 64
(dst / 'f').write_bytes(bytes(prefix))

# --no-whole-file forces the delta/checksum path regardless of local-vs-remote.
# run_rsync(check=True) fails the test on the non-zero exit the bug produced.
run_rsync('-a', '--append-verify', '--checksum-choice=xxh64', '--no-whole-file',
          f'{src}/', f'{dst}/')
assert_same(dst / 'f', src / 'f', label='append-verify xxh64 redo')

print("append-shortsum: --append-verify with an 8-byte (xxh64) checksum no "
      "longer overflows the block s2length")

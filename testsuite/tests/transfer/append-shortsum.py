#!/usr/bin/env python3

import json

from harness.rsync import (
    FROMDIR, TODIR, assert_same, make_data_file, rmtree, run_rsync,
    test_skipped,
)

vv = json.loads(run_rsync('-VV', check=True, capture_output=True).stdout)
if 'xxh64' not in vv.get('checksum_list', []):
    test_skipped("xxh64 not in this build's checksum list (no xxhash)", capability='xxhash')

src, dst = FROMDIR, TODIR
rmtree(src)
rmtree(dst)
src.mkdir(parents=True)
dst.mkdir(parents=True)

make_data_file(src / 'f', 40000)
full = (src / 'f').read_bytes()
prefix = bytearray(full[:20000])
prefix[0:64] = b'\x00' * 64
(dst / 'f').write_bytes(bytes(prefix))

run_rsync('-a', '--append-verify', '--checksum-choice=xxh64', '--no-whole-file',
          f'{src}/', f'{dst}/')
assert_same(dst / 'f', src / 'f', label='append-verify xxh64 redo')

print("append-shortsum: --append-verify with an 8-byte (xxh64) checksum no "
      "longer overflows the block s2length")

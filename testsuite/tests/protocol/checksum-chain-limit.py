#!/usr/bin/env python3

import re

from harness.rsync import (
    FROMDIR, TODIR, assert_same, rmtree, run_rsync, test_fail,
)

BLOCK = 256
NBLOCKS = 8000
RUN = 3000
C = 100

MAX_FALSE_ALARMS_PER_HIT = NBLOCKS // 4

def make_decoy_block() -> bytes:
    b = bytearray([C]) * BLOCK
    b[0] = C + 1
    b[1] = C - 2
    b[2] = C + 1
    return bytes(b)

rmtree(FROMDIR)
rmtree(TODIR)
FROMDIR.mkdir(parents=True, exist_ok=True)
TODIR.mkdir(parents=True, exist_ok=True)

src = FROMDIR / 'image.bin'
dst = TODIR / 'image.bin'

decoy = make_decoy_block()
with open(dst, 'wb') as f:
    for _ in range(NBLOCKS):
        f.write(decoy)

with open(src, 'wb') as f:
    f.write(bytes([C]) * RUN)

proc = run_rsync('-a', '--no-whole-file', f'--block-size={BLOCK}',
                 '--no-compress', '--debug=deltasum1',
                 f'{src}', f'{dst}', capture_output=True)

out = proc.stdout + proc.stderr
m = re.search(r'hash_hits=(\d+)\s+false_alarms=(\d+)', out)
if not m:
    test_fail(f"could not find deltasum stats in rsync output:\n{out}")
hash_hits = int(m.group(1))
false_alarms = int(m.group(2))

if hash_hits == 0:
    test_fail("expected the source to hit the decoy chain but hash_hits=0")

ratio = false_alarms / hash_hits
if ratio > MAX_FALSE_ALARMS_PER_HIT:
    test_fail(
        f"hash_search() walked ~{ratio:.0f} chain entries per hash hit "
        f"(false_alarms={false_alarms}, hash_hits={hash_hits}); the chain of "
        f"{NBLOCKS} entries is not being capped -- issue #217 regression")

assert_same(dst, src, label='issue #217 chain-cap transfer')

print(f"issue #217: bounded at {ratio:.0f} false alarms/hit "
      f"(chain={NBLOCKS}, cap keeps it under {MAX_FALSE_ALARMS_PER_HIT})")

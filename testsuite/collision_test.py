#!/usr/bin/env python3
"""Combined regression test for hash_search() weak-checksum collisions.

Tests both collision pathologies in a single transfer without wall-clock timers:

1. Duplicate-chain unlinking (issue #1115):
   The basis file contains 8 blocks of A followed by 1,500 identical blocks of B
   that share the same weak checksum. Because B is added last, all 1,500 B blocks
   sit in front of A on the hash chain (> MAX_CHAIN_LEN = 1024). Unlinking
   duplicate false-alarm nodes on the first mismatch allows hash_search() to
   reach and match all 8 blocks of A (verified via `Matched data`).

2. Consecutive false-offset block skipping:
   The source file then has 10 blocks of C, which also share the same weak
   checksum but do not exist in the basis file. Rather than rolling 1 byte at
   a time and computing a strong checksum at all ~6,337 byte offsets, skipping
   a block after 4 consecutive false offsets keeps `hash_hits` bounded at O(N/B).
"""

import re

from rsyncfns import (
    FROMDIR, TODIR, assert_same, rmtree, run_rsync, test_fail,
)

BLOCK = 704          # Valid block size (>= 700, multiple of 8)
N_MATCH = 8          # Blocks of A that must match behind the chain
N_DUP_CHAIN = 1500   # Duplicate B blocks in front of A (> MAX_CHAIN_LEN 1024)
N_FLOOD = 10         # Unmatched C blocks that collide on every byte offset
C_BYTE = 100


def make_block(perturb_offset: int = -1) -> bytes:
    b = bytearray([C_BYTE]) * BLOCK
    if perturb_offset >= 0:
        # (+1, -2, +1) preserves get_checksum1 (both s1 and s2) while
        # producing a distinct strong checksum.
        b[perturb_offset] = C_BYTE + 1
        b[perturb_offset + 1] = C_BYTE - 2
        b[perturb_offset + 2] = C_BYTE + 1
    return bytes(b)


rmtree(FROMDIR)
rmtree(TODIR)
FROMDIR.mkdir(parents=True, exist_ok=True)
TODIR.mkdir(parents=True, exist_ok=True)

src = FROMDIR / 'image.bin'
dst = TODIR / 'image.bin'

block_a = make_block(-1)   # Pure C_BYTE
block_b = make_block(0)    # Perturbed at 0..2 (same sum1, different sum2)
block_c = make_block(4)    # Perturbed at 4..6 (same sum1, different sum2)

# Basis: 8 blocks of A buried behind 1,500 duplicate blocks of B.
dst.write_bytes((block_a * N_MATCH) + (block_b * N_DUP_CHAIN))

# Source: 8 blocks of A (should match!) + 10 blocks of C (unmatched collision flood).
src.write_bytes((block_a * N_MATCH) + (block_c * N_FLOOD))

proc = run_rsync('-a', '-I', '--no-whole-file', f'--block-size={BLOCK}',
                 '--no-compress', '--stats', '--debug=deltasum1',
                 f'{src}', f'{dst}', capture_output=True)

out = proc.stdout + proc.stderr

m_delta = re.search(r'hash_hits=(\d+)\s+false_alarms=(\d+)', out)
m_match = re.search(r'Matched data:\s*([\d,]+)\s*bytes', out)
if not m_delta or not m_match:
    test_fail(f"could not find deltasum/stats in rsync output:\n{out}")

hash_hits = int(m_delta.group(1))
false_alarms = int(m_delta.group(2))
matched_bytes = int(m_match.group(1).replace(',', ''))

expected_matched = N_MATCH * BLOCK  # 5,632 bytes

# 1. Check issue #1115 (must reach and match all 8 blocks of A behind 1,500 B blocks)
if matched_bytes != expected_matched:
    test_fail(
        f"issue #1115 regression: matched {matched_bytes} bytes "
        f"(expected {expected_matched}); valid blocks behind {N_DUP_CHAIN} "
        f"duplicate chain entries were cut off by MAX_CHAIN_LEN")

# 2. Check consecutive false-offset block skip (must not roll 1 byte at a time across C)
# Without block skipping, hash_hits is ~6,345 (8 + 9*704 + 1).
# With block skipping after 4 consecutive misses, hash_hits is 21 (8 + 4 + 9).
MAX_EXPECTED_HITS = (N_MATCH + N_FLOOD) * 4  # 72 << 6,345

if hash_hits > MAX_EXPECTED_HITS:
    test_fail(
        f"collision flood rolled 1 byte at a time: hash_hits={hash_hits} "
        f"(expected <= {MAX_EXPECTED_HITS}, false_alarms={false_alarms})")

assert_same(dst, src, label='combined collision transfer')

print(f"collision test passed: matched={matched_bytes} bytes, "
      f"hash_hits={hash_hits} (<= {MAX_EXPECTED_HITS}), "
      f"false_alarms={false_alarms}")

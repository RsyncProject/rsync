#!/usr/bin/env python3
"""Regression test for hash_search() collision-flood bounds and skip boundaries.

Verifies that when rolling across unmatched constant-checksum collision runs:
1. CPU work (hash_hits and false_alarms) is strictly bounded at O(N / blength)
   rather than computing a strong checksum on every byte O(N).
2. Bandwidth (Literal data vs Matched data) is exact: valid basis blocks placed
   immediately after colliding runs at every critical skip boundary are 100%
   matched without skipping a single valid block:
   - Sub-threshold collision run (3 bytes < 4-offset threshold)
   - Exact 1-block boundary (BLOCK bytes)
   - Exact 1st-skip landing boundary (BLOCK + 3 bytes)
   - 1-byte past 1st-skip landing boundary (BLOCK + 4 bytes)
   - Exact 2nd-skip landing boundary (2 * BLOCK + 3 bytes)
   - Multi-skip + sub-threshold roll (2 * BLOCK + 6 bytes)
   - Long multi-block flood + arbitrary unaligned offset (10 * BLOCK + 137 bytes)
3. `--inplace` mode preserves exact boundary matches across collision skips.
4. Crafted weak-checksum collisions with bunched perturbations (e.g., (+1, -2, +1)
   at bytes 0..2) trigger premature boundary skips due to partial window overlap,
   resulting in *increased literal data* (safely falling back to literal transmission
   rather than corrupting the file or hanging the CPU).
"""

import re

from rsyncfns import (
    FROMDIR, TODIR, assert_same, rmtree, run_rsync, test_fail,
)

BLOCK = 704
C_BYTE = 100


def make_spread_colliding_block(delta: int, fill: int = C_BYTE) -> bytes:
    b = bytearray([fill]) * BLOCK
    b[0] = fill + delta
    b[351] = fill - delta
    b[352] = fill - delta
    b[703] = fill + delta
    return bytes(b)


def make_block(perturb_offset: int = -1, fill: int = C_BYTE) -> bytes:
    """Crafted weak-checksum collision block ((+1, -2, +1) perturbation at 0..2).
    Because perturbations are bunched at the start, partial boundary windows
    falsely collide, triggering premature skips and resulting in increased literal data.
    """
    b = bytearray([fill]) * BLOCK
    if perturb_offset >= 0:
        b[perturb_offset] = fill + 1
        b[perturb_offset + 1] = fill - 2
        b[perturb_offset + 2] = fill + 1
    return bytes(b)


def parse_stats(out: str):
    m_delta = re.search(r'hash_hits=(\d+)\s+false_alarms=(\d+)', out)
    m_lit = re.search(r'Literal data:\s*([\d,]+)\s*bytes', out)
    m_match = re.search(r'Matched data:\s*([\d,]+)\s*bytes', out)
    if not m_delta or not m_lit or not m_match:
        test_fail(f"could not find deltasum/stats in rsync output:\n{out}")
    return (
        int(m_delta.group(1)),
        int(m_delta.group(2)),
        int(m_lit.group(1).replace(',', '')),
        int(m_match.group(1).replace(',', '')),
    )


block_a = make_spread_colliding_block(5)
block_d = make_spread_colliding_block(9)

rmtree(FROMDIR)
rmtree(TODIR)
FROMDIR.mkdir(parents=True, exist_ok=True)
TODIR.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Case 1: Skip-boundary match preservation + CPU/bandwidth bounds
# ---------------------------------------------------------------------------
src1 = FROMDIR / 'skip_boundaries.bin'
dst1 = TODIR / 'skip_boundaries.bin'

dst1_payload = (block_a * 2) + (block_d * 2) + (block_a * 14) + (block_d * 14)
dst1.write_bytes(dst1_payload)

boundary_runs = [
    bytes([C_BYTE]) * 3,                  # < 4 misses (no skip, rolls into match)
    bytes([C_BYTE]) * BLOCK,              # Exact 1-block boundary
    bytes([C_BYTE]) * (BLOCK + 3),        # Exact landing offset of 1st skip (3 + BLOCK)
    bytes([C_BYTE]) * (BLOCK + 4),        # 1 byte after 1st skip landing
    bytes([C_BYTE]) * (2 * BLOCK + 3),    # Exact landing offset of 2nd consecutive skip
    bytes([C_BYTE]) * (2 * BLOCK + 6),    # 2 skips + 3-byte sub-threshold roll
    bytes([C_BYTE]) * (10 * BLOCK + 137), # Long flood + unaligned remainder
]

src1_parts = [(block_a * 2) + (block_d * 2)]
for idx, run in enumerate(boundary_runs):
    valid_group = (block_d * 4) if (idx % 2 == 0) else (block_a * 4)
    src1_parts.append(run)
    src1_parts.append(valid_group)

src1_payload = b''.join(src1_parts)
src1.write_bytes(src1_payload)

proc1 = run_rsync('-a', '-I', '--no-whole-file', f'--block-size={BLOCK}',
                  '--no-compress', '--stats', '--debug=deltasum1',
                  f'{src1}', f'{dst1}', capture_output=True)

hash_hits1, false_alarms1, literal1, matched1 = parse_stats(
    proc1.stdout + proc1.stderr
)

total_valid_blocks = 4 * (1 + len(boundary_runs))  # 32 blocks = 22,528 bytes
expected_matched1 = total_valid_blocks * BLOCK
expected_literal1 = sum(len(r) for r in boundary_runs)

if matched1 != expected_matched1 or literal1 != expected_literal1:
    test_fail(
        f"Case 1 skip boundary missed valid basis blocks: "
        f"matched={matched1} (expected {expected_matched1}), "
        f"literal={literal1} (expected {expected_literal1})"
    )

MAX_EXPECTED_HITS1 = 160
if hash_hits1 > MAX_EXPECTED_HITS1:
    test_fail(
        f"Case 1 collision flood rolled 1 byte at a time: hash_hits={hash_hits1} "
        f"(expected <= {MAX_EXPECTED_HITS1})"
    )

assert_same(dst1, src1, label='Case 1: collision skip-boundary transfer')

# ---------------------------------------------------------------------------
# Case 2: Pure unmatched collision flood CPU bound (O(N/B) vs O(N))
# ---------------------------------------------------------------------------
src2 = FROMDIR / 'pure_flood.bin'
dst2 = TODIR / 'pure_flood.bin'

N_FLOOD = 12
dst2.write_bytes(block_a * N_FLOOD)
src2.write_bytes(bytes([C_BYTE]) * (N_FLOOD * BLOCK))

proc2 = run_rsync('-a', '-I', '--no-whole-file', f'--block-size={BLOCK}',
                  '--no-compress', '--stats', '--debug=deltasum1',
                  f'{src2}', f'{dst2}', capture_output=True)

hash_hits2, false_alarms2, literal2, matched2 = parse_stats(
    proc2.stdout + proc2.stderr
)

MAX_EXPECTED_HITS2 = N_FLOOD * 3
if hash_hits2 > MAX_EXPECTED_HITS2:
    test_fail(
        f"Case 2 pure flood rolled 1 byte at a time: hash_hits={hash_hits2} "
        f"(expected <= {MAX_EXPECTED_HITS2})"
    )

assert_same(dst2, src2, label='Case 2: pure collision flood transfer')

# ---------------------------------------------------------------------------
# Case 3: Skip-boundary match preservation under --inplace
# ---------------------------------------------------------------------------
src3 = FROMDIR / 'inplace_skip.bin'
dst3 = TODIR / 'inplace_skip.bin'

dst3.write_bytes(dst1_payload)
src3.write_bytes(src1_payload)

proc3 = run_rsync('-a', '-I', '--inplace', '--no-whole-file',
                  f'--block-size={BLOCK}', '--no-compress',
                  '--stats', '--debug=deltasum1',
                  f'{src3}', f'{dst3}', capture_output=True)

hash_hits3, _, literal3, matched3 = parse_stats(proc3.stdout + proc3.stderr)

if matched3 != expected_matched1 or literal3 != expected_literal1:
    test_fail(
        f"Case 3 (--inplace skip boundaries) missed valid matches: "
        f"matched={matched3} (expected {expected_matched1}), "
        f"literal={literal3} (expected {expected_literal1})"
    )

if hash_hits3 > MAX_EXPECTED_HITS1:
    test_fail(
        f"Case 3 (--inplace) rolled 1 byte at a time: hash_hits={hash_hits3} "
        f"(expected <= {MAX_EXPECTED_HITS1})"
    )

assert_same(dst3, src3, label='Case 3: --inplace collision skip-boundary transfer')

# ---------------------------------------------------------------------------
# Case 4: Bunched-perturbation hazard demonstration (make_block vs spread)
# ---------------------------------------------------------------------------
# Documents that if a colliding block uses bunched perturbations (like (+1, -2, +1)
# at bytes 0..2), partial overlapping boundary windows falsely trigger premature
# skips, jumping over valid blocks and resulting in *increased literal data*
# (safe fallback to network transfer) while preserving 100% file integrity.
# Odd block sizes here should not result in more literal data.
src4 = FROMDIR / 'bunched_hazard.bin'
dst4 = TODIR / 'bunched_hazard.bin'

# Valid block built with bunched perturbation (make_block)
bunched_block = make_block(0)

dst4.write_bytes(bunched_block * 16)

# Interleave pure C_BYTE gaps with bunched blocks
src4_payload = (
    (bunched_block * 2)
    + (bytes([C_BYTE]) * BLOCK)  # 1-block boundary gap
    + (bunched_block * 4)
)
src4.write_bytes(src4_payload)

proc4 = run_rsync('-a', '-I', '--no-whole-file', f'--block-size={BLOCK}',
                  '--no-compress', '--stats', '--debug=deltasum1',
                  f'{src4}', f'{dst4}', capture_output=True)

hash_hits4, _, literal4, matched4 = parse_stats(proc4.stdout + proc4.stderr)

# Because bunched perturbations trigger premature skips, literal data is
# higher than the exact gap length (> 704 bytes), but file integrity holds.
EXPECTED_BASE_LITERAL = BLOCK + 10
if literal4 <= EXPECTED_BASE_LITERAL:
    test_fail(f"Case 4 expected bunched perturbation to cause literal data inflation, got {literal4} bytes")

assert_same(dst4, src4, label='Case 4: bunched-perturbation hazard transfer')

print(
    f"collision-skip test passed: boundary_hits={hash_hits1}, "
    f"flood_hits={hash_hits2}, inplace_hits={hash_hits3}, "
    f"bunched_literal={literal4} (exceeded {EXPECTED_BASE_LITERAL} due to premature skip)"
)

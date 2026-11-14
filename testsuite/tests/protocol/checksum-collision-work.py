#!/usr/bin/env python3

from fixtures.checksum import BLOCK, C_BYTE, spread_colliding_block, transfer_stats
from harness.environment import FROMDIR, TODIR
from harness.filesystem import assert_same, rmtree
from harness.results import test_fail
from harness.rsync import run_rsync
from harness import metadata

metadata(transports={'pipe'})

block_a = spread_colliding_block(5)
block_d = spread_colliding_block(9)

rmtree(FROMDIR)
rmtree(TODIR)
FROMDIR.mkdir(parents=True, exist_ok=True)
TODIR.mkdir(parents=True, exist_ok=True)

# Matches immediately after each skip boundary must be retained.
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

hash_hits1, false_alarms1, literal1, matched1 = transfer_stats(
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

# An unmatched collision flood must remain bounded by block count.
src2 = FROMDIR / 'pure_flood.bin'
dst2 = TODIR / 'pure_flood.bin'

N_FLOOD = 12
dst2.write_bytes(block_a * N_FLOOD)
src2.write_bytes(bytes([C_BYTE]) * (N_FLOOD * BLOCK))

proc2 = run_rsync('-a', '-I', '--no-whole-file', f'--block-size={BLOCK}',
                  '--no-compress', '--stats', '--debug=deltasum1',
                  f'{src2}', f'{dst2}', capture_output=True)

hash_hits2, false_alarms2, literal2, matched2 = transfer_stats(
    proc2.stdout + proc2.stderr
)

MAX_EXPECTED_HITS2 = N_FLOOD * 3
if hash_hits2 > MAX_EXPECTED_HITS2:
    test_fail(
        f"Case 2 pure flood rolled 1 byte at a time: hash_hits={hash_hits2} "
        f"(expected <= {MAX_EXPECTED_HITS2})"
    )

assert_same(dst2, src2, label='Case 2: pure collision flood transfer')

# The same boundary matches must survive an in-place update.
src3 = FROMDIR / 'inplace_skip.bin'
dst3 = TODIR / 'inplace_skip.bin'

dst3.write_bytes(dst1_payload)
src3.write_bytes(src1_payload)

proc3 = run_rsync('-a', '-I', '--inplace', '--no-whole-file',
                  f'--block-size={BLOCK}', '--no-compress',
                  '--stats', '--debug=deltasum1',
                  f'{src3}', f'{dst3}', capture_output=True)

hash_hits3, _, literal3, matched3 = transfer_stats(proc3.stdout + proc3.stderr)

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

print(
    f"collision-skip test passed: boundary_hits={hash_hits1}, "
    f"flood_hits={hash_hits2}, inplace_hits={hash_hits3}"
)

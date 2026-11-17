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

# Tiny blength weak collisions must still step by at least BLOCK_SIZE - 1.
src4 = FROMDIR / 'tiny_blength_flood.bin'
dst4 = TODIR / 'tiny_blength_flood.bin'

TINY_B = 3
tiny_colliding_block = bytes([C_BYTE + 1, C_BYTE - 2, C_BYTE + 1])
N_TINY_FLOOD = 16
expected_literal4 = N_TINY_FLOOD * BLOCK
dst4.write_bytes(tiny_colliding_block * N_TINY_FLOOD)
src4.write_bytes(bytes([C_BYTE]) * expected_literal4)

proc4 = run_rsync('-a', '-I', '--no-whole-file', f'--block-size={TINY_B}',
                  '--no-compress', '--stats', '--debug=deltasum1',
                  f'{src4}', f'{dst4}', capture_output=True)

hash_hits4, false_alarms4, literal4, matched4 = transfer_stats(
    proc4.stdout + proc4.stderr
)

MAX_EXPECTED_HITS4 = N_TINY_FLOOD * 4
if hash_hits4 > MAX_EXPECTED_HITS4 or matched4 != 0 or literal4 != expected_literal4:
    test_fail(
        f"Case 4 tiny blength={TINY_B} failed to enforce skip floor: "
        f"hash_hits={hash_hits4} (expected <= {MAX_EXPECTED_HITS4}), "
        f"matched={matched4} (expected 0), literal={literal4} (expected {expected_literal4})"
    )

assert_same(dst4, src4, label='Case 4: tiny block-size collision skip transfer')

# Bucket collisions with mismatched sum1 must cap at MAX_CHAIN_LEN and skip.
src5 = FROMDIR / 'bucket_chain_flood.bin'
dst5 = TODIR / 'bucket_chain_flood.bin'

BUCKET_B = 2
bucket_colliding_block = bytes([C_BYTE - 2, C_BYTE + 3])
N_BUCKET_BLOCKS = 1200  # > MAX_CHAIN_LEN (1024)
expected_literal5 = N_TINY_FLOOD * BLOCK
dst5.write_bytes(bucket_colliding_block * N_BUCKET_BLOCKS)
src5.write_bytes(bytes([C_BYTE]) * expected_literal5)

proc5 = run_rsync('-a', '-I', '--no-whole-file', f'--block-size={BUCKET_B}',
                  '--no-compress', '--stats', '--debug=deltasum1',
                  f'{src5}', f'{dst5}', capture_output=True)

hash_hits5, false_alarms5, literal5, matched5 = transfer_stats(
    proc5.stdout + proc5.stderr
)

MAX_EXPECTED_HITS5 = N_TINY_FLOOD * 4
if (
    hash_hits5 > MAX_EXPECTED_HITS5
    or false_alarms5 != 0
    or matched5 != 0
    or literal5 != expected_literal5
):
    test_fail(
        f"Case 5 bucket chain collision failed to cap and skip: "
        f"hash_hits={hash_hits5} (expected <= {MAX_EXPECTED_HITS5}), "
        f"false_alarms={false_alarms5} (expected 0), matched={matched5} (expected 0), "
        f"literal={literal5} (expected {expected_literal5})"
    )

assert_same(dst5, src5, label='Case 5: bucket chain-cap skip transfer')

# Sub-cap bucket chain (<= MAX_CHAIN_LEN) with mismatched sum1 must still skip.
src6 = FROMDIR / 'subcap_bucket_flood.bin'
dst6 = TODIR / 'subcap_bucket_flood.bin'

N_SUBCAP_BLOCKS = 1024  # Exactly MAX_CHAIN_LEN (chain_len > MAX_CHAIN_LEN is False)
expected_literal6 = N_TINY_FLOOD * BLOCK
dst6.write_bytes(bucket_colliding_block * N_SUBCAP_BLOCKS)
src6.write_bytes(bytes([C_BYTE]) * expected_literal6)

proc6 = run_rsync('-a', '-I', '--no-whole-file', f'--block-size={BUCKET_B}',
                  '--no-compress', '--stats', '--debug=deltasum1',
                  f'{src6}', f'{dst6}', capture_output=True)

hash_hits6, false_alarms6, literal6, matched6 = transfer_stats(
    proc6.stdout + proc6.stderr
)

MAX_EXPECTED_HITS6 = N_TINY_FLOOD * 4
if (
    hash_hits6 > MAX_EXPECTED_HITS6
    or false_alarms6 != 0
    or matched6 != 0
    or literal6 != expected_literal6
):
    test_fail(
        f"Case 6 sub-cap bucket chain (1024) bypassed skip bound: "
        f"hash_hits={hash_hits6} (expected <= {MAX_EXPECTED_HITS6}), "
        f"false_alarms={false_alarms6} (expected 0), matched={matched6} (expected 0), "
        f"literal={literal6} (expected {expected_literal6})"
    )

assert_same(dst6, src6, label='Case 6: sub-cap bucket chain skip transfer')

# Deep collision chain ending in a tiny match must not walk the full chain per tiny step.
src7 = FROMDIR / 'tail_match_chain_flood.bin'
dst7 = TODIR / 'tail_match_chain_flood.bin'

expected_total7 = N_TINY_FLOOD * BLOCK
dst7.write_bytes(bytes([C_BYTE, C_BYTE]) + (bucket_colliding_block * 1023))
src7.write_bytes(bytes([C_BYTE]) * expected_total7)

proc7 = run_rsync('-a', '-I', '--no-whole-file', f'--block-size={BUCKET_B}',
                  '--no-compress', '--stats', '--debug=deltasum1',
                  f'{src7}', f'{dst7}', capture_output=True)

hash_hits7, false_alarms7, literal7, matched7 = transfer_stats(
    proc7.stdout + proc7.stderr
)

MAX_EXPECTED_HITS7 = N_TINY_FLOOD * 4
if (
    hash_hits7 > MAX_EXPECTED_HITS7
    or false_alarms7 != 0
    or matched7 + literal7 != expected_total7
    or literal7 <= expected_total7 - (MAX_EXPECTED_HITS7 * BUCKET_B)
):
    test_fail(
        f"Case 7 deep chain with tiny tail match bypassed skip bound: "
        f"hash_hits={hash_hits7} (expected <= {MAX_EXPECTED_HITS7}), "
        f"false_alarms={false_alarms7} (expected 0), "
        f"matched={matched7}, literal={literal7} (total expected {expected_total7})"
    )

assert_same(dst7, src7, label='Case 7: deep chain tiny tail-match transfer')

print(
    f"collision-skip test passed: boundary_hits={hash_hits1}, "
    f"flood_hits={hash_hits2}, inplace_hits={hash_hits3}, "
    f"tiny_blength_hits={hash_hits4}, bucket_chain_hits={hash_hits5}, "
    f"subcap_hits={hash_hits6}, tail_match_hits={hash_hits7}"
)

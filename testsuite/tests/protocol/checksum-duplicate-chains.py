#!/usr/bin/env python3

from fixtures.checksum import BLOCK, colliding_block, transfer_stats
from harness.environment import FROMDIR, TODIR
from harness.filesystem import assert_same, rmtree
from harness.results import test_fail
from harness.rsync import run_rsync
from harness import metadata

metadata(transports={'pipe'})

block_a = colliding_block(0)
block_b = colliding_block(4)
block_d = colliding_block(8)
block_e = colliding_block(12)

block_x = colliding_block(fill=88)
block_y = colliding_block(fill=69)

rmtree(FROMDIR)
rmtree(TODIR)
FROMDIR.mkdir(parents=True, exist_ok=True)
TODIR.mkdir(parents=True, exist_ok=True)

# Valid blocks behind a chain longer than MAX_CHAIN_LEN (#1115).
src1 = FROMDIR / 'forward1115.bin'
dst1 = TODIR / 'forward1115.bin'

N_HEAD = 8
N_DUP = 1500  # > MAX_CHAIN_LEN (1024)

payload1 = (block_a * N_HEAD) + (block_b * N_DUP)
src1.write_bytes(payload1)
dst1.write_bytes(payload1)

proc1 = run_rsync('-a', '-I', '--no-whole-file', f'--block-size={BLOCK}',
                  '--no-compress', '--stats', '--debug=deltasum1',
                  f'{src1}', f'{dst1}', capture_output=True)

_, false_alarms1, literal1, matched1 = transfer_stats(proc1.stdout + proc1.stderr)

if literal1 != 0 or matched1 != len(payload1):
    test_fail(
        f"Case 1 (#1115 forward) failed: literal={literal1} (expected 0), "
        f"matched={matched1} (expected {len(payload1)})")

if false_alarms1 > N_HEAD * 2:
    test_fail(
        f"Case 1 duplicate chain entries were not unlinked: "
        f"false_alarms={false_alarms1} (expected <= {N_HEAD * 2})")

assert_same(dst1, src1, label='Case 1: #1115 forward layout')

# Several colliding groups in one overlong chain.
src2 = FROMDIR / 'interleaved1115.bin'
dst2 = TODIR / 'interleaved1115.bin'

dst2_payload = (block_a * 4) + (block_b * 600) + (block_d * 4) + (block_e * 600)
src2_payload = (block_a * 4) + (block_d * 4) + (block_b * 4) + (block_e * 4)

dst2.write_bytes(dst2_payload)
src2.write_bytes(src2_payload)

proc2 = run_rsync('-a', '-I', '--no-whole-file', f'--block-size={BLOCK}',
                  '--no-compress', '--stats', '--debug=deltasum1',
                  f'{src2}', f'{dst2}', capture_output=True)

_, false_alarms2, literal2, matched2 = transfer_stats(proc2.stdout + proc2.stderr)

if literal2 != 0 or matched2 != len(src2_payload):
    test_fail(
        f"Case 2 (multi-group chain) failed: literal={literal2} (expected 0), "
        f"matched={matched2} (expected {len(src2_payload)})")

if false_alarms2 > 64:
    test_fail(
        f"Case 2 excessive false_alarms={false_alarms2} (expected <= 64)")

assert_same(dst2, src2, label='Case 2: multi-group duplicate unlinking')

# An unpinned duplicate must not unlink a pinned same-offset block.
src3 = FROMDIR / 'inplace_preserve.bin'
dst3 = TODIR / 'inplace_preserve.bin'

N_TAIL = 20

dst3.write_bytes(block_a + block_y + block_a + (block_y * N_TAIL) + (block_b * 1200))
src3.write_bytes(block_a + block_d + block_x + (block_a * N_TAIL))

proc3 = run_rsync('-a', '-I', '--inplace', '--no-whole-file',
                  f'--block-size={BLOCK}', '--no-compress',
                  '--stats', '--debug=deltasum1',
                  f'{src3}', f'{dst3}', capture_output=True)

_, _, literal3, matched3 = transfer_stats(proc3.stdout + proc3.stderr)

expected_literal3 = 2 * BLOCK          # Only Block 1 (D) and Block 2 (X)
expected_matched3 = (1 + N_TAIL) * BLOCK  # Block 0 (A) + 20 trailing A blocks

if literal3 != expected_literal3 or matched3 != expected_matched3:
    test_fail(
        f"Case 3 (--inplace SUMFLG_SAME_OFFSET preservation) failed: "
        f"literal={literal3} (expected {expected_literal3}), "
        f"matched={matched3} (expected {expected_matched3})")

assert_same(dst3, src3, label='Case 3: --inplace SUMFLG_SAME_OFFSET preservation')

# Pinned duplicates must be unlinked before a later valid block is sought.
src4 = FROMDIR / 'inplace_pinned_dups.bin'
dst4 = TODIR / 'inplace_pinned_dups.bin'

N_PINNED_DUPS = 1500  # > MAX_CHAIN_LEN (1024)
N_TRAIL_A = 16

dst4.write_bytes(block_a + (block_b * N_PINNED_DUPS) + block_a)
src4.write_bytes(block_a + (block_b * N_PINNED_DUPS) + block_x + (block_a * N_TRAIL_A))

proc4 = run_rsync('-a', '-I', '--inplace', '--no-whole-file',
                  f'--block-size={BLOCK}', '--no-compress',
                  '--stats', '--debug=deltasum1',
                  f'{src4}', f'{dst4}', capture_output=True)

_, false_alarms4, literal4, matched4 = transfer_stats(proc4.stdout + proc4.stderr)

expected_literal4 = 1 * BLOCK             # Only Block 1501 (X)
expected_matched4 = (1 + N_PINNED_DUPS + N_TRAIL_A) * BLOCK

if literal4 != expected_literal4 or matched4 != expected_matched4:
    test_fail(
        f"Case 4 (--inplace pinned duplicate unlinking) failed: "
        f"literal={literal4} (expected {expected_literal4}), "
        f"matched={matched4} (expected {expected_matched4})")

MAX_EXPECTED_ALARMS4 = N_PINNED_DUPS + (N_TRAIL_A * 2)
if false_alarms4 > MAX_EXPECTED_ALARMS4:
    test_fail(
        f"Case 4 pinned duplicates were not unlinked: "
        f"false_alarms={false_alarms4} (expected <= {MAX_EXPECTED_ALARMS4})")

assert_same(dst4, src4, label='Case 4: --inplace pinned duplicate unlinking')

print(
    f"dup-chain test passed: forward_alarms={false_alarms1}, "
    f"interleaved_alarms={false_alarms2}, inplace_matched={matched3}, "
    f"pinned_dups_matched={matched4} (alarms={false_alarms4})"
)

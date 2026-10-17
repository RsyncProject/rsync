"""Regression test for duplicate-chain unlinking in hash_search() (issue #1115).

Covers:
1. Forward layout (#1115): valid blocks at the beginning of the basis file
   sitting behind > MAX_CHAIN_LEN (1024) duplicate colliding blocks.
2. Multi-group interleaved layout: multiple distinct valid block types (A, D)
   buried behind > MAX_CHAIN_LEN duplicate colliding blocks (B, E) in the same
   hash bucket.
3. `--inplace` SUMFLG_SAME_OFFSET preservation: mutating the chain on duplicate
   false alarms must never unlink a basis block carrying SUMFLG_SAME_OFFSET,
   even when a later duplicate in the basis file gets overwritten on disk.
"""

import re

from rsyncfns import (
    FROMDIR, TODIR, assert_same, rmtree, run_rsync, test_fail,
)

BLOCK = 704
C_BYTE = 100


def make_block(perturb_offset: int = -1, fill: int = C_BYTE) -> bytes:
    b = bytearray([fill]) * BLOCK
    if perturb_offset >= 0:
        # (+1, -2, +1) preserves get_checksum1 (both s1 and s2) while
        # producing a distinct strong checksum.
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


# Blocks A, B, D, E all share the exact same 32-bit weak checksum (sum1)
# but have distinct strong checksums (sum2).
block_a = make_block(0)
block_b = make_block(4)
block_d = make_block(8)
block_e = make_block(12)

# Distinct non-colliding blocks for --inplace overwrite testing
block_x = make_block(-1, fill=88)
block_y = make_block(-1, fill=69)

rmtree(FROMDIR)
rmtree(TODIR)
FROMDIR.mkdir(parents=True, exist_ok=True)
TODIR.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Case 1: Forward layout (#1115) -- Valid blocks behind 1,500 duplicate B blocks
# ---------------------------------------------------------------------------
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

_, false_alarms1, literal1, matched1 = parse_stats(proc1.stdout + proc1.stderr)

if literal1 != 0 or matched1 != len(payload1):
    test_fail(
        f"Case 1 (#1115 forward) failed: literal={literal1} (expected 0), "
        f"matched={matched1} (expected {len(payload1)})")

if false_alarms1 > N_HEAD * 2:
    test_fail(
        f"Case 1 duplicate chain entries were not unlinked: "
        f"false_alarms={false_alarms1} (expected <= {N_HEAD * 2})")

assert_same(dst1, src1, label='Case 1: #1115 forward layout')

# ---------------------------------------------------------------------------
# Case 2: Multi-group interleaved duplicates in a single hash chain
# ---------------------------------------------------------------------------
src2 = FROMDIR / 'interleaved1115.bin'
dst2 = TODIR / 'interleaved1115.bin'

# Basis chain has 4 colliding groups stacked > MAX_CHAIN_LEN deep:
# [600 x E] -> [4 x D] -> [600 x B] -> [4 x A]  (total chain = 1,208 > 1024)
dst2_payload = (block_a * 4) + (block_b * 600) + (block_d * 4) + (block_e * 600)
# Source requests A first, then D, then B, then E:
src2_payload = (block_a * 4) + (block_d * 4) + (block_b * 4) + (block_e * 4)

dst2.write_bytes(dst2_payload)
src2.write_bytes(src2_payload)

proc2 = run_rsync('-a', '-I', '--no-whole-file', f'--block-size={BLOCK}',
                  '--no-compress', '--stats', '--debug=deltasum1',
                  f'{src2}', f'{dst2}', capture_output=True)

_, false_alarms2, literal2, matched2 = parse_stats(proc2.stdout + proc2.stderr)

if literal2 != 0 or matched2 != len(src2_payload):
    test_fail(
        f"Case 2 (multi-group chain) failed: literal={literal2} (expected 0), "
        f"matched={matched2} (expected {len(src2_payload)})")

if false_alarms2 > 64:
    test_fail(
        f"Case 2 excessive false_alarms={false_alarms2} (expected <= 64)")

assert_same(dst2, src2, label='Case 2: multi-group duplicate unlinking')

# ---------------------------------------------------------------------------
# Case 3: --inplace SUMFLG_SAME_OFFSET preservation when mutating chain
# ---------------------------------------------------------------------------
src3 = FROMDIR / 'inplace_preserve.bin'
dst3 = TODIR / 'inplace_preserve.bin'

N_TAIL = 20

# Basis has A at Block 0 (offset 0) and Block 2 (offset 2*BLOCK), plus 1,200
# duplicate B blocks at the end so the chain is > MAX_CHAIN_LEN:
# Chain order: [1200 x B] -> [Block 2: A] -> [Block 0: A]
dst3.write_bytes(block_a + block_y + block_a + (block_y * N_TAIL) + (block_b * 1200))

# Source:
# - Block 0: A (matches Block 0 in-place -> sets SUMFLG_SAME_OFFSET on Block 0,
#   and unlinks the 1,199 duplicate B blocks ahead of it)
# - Block 1: D (collides with A & B -> walks [B -> Block 2 (A) -> Block 0 (A)];
#   must NOT unlink Block 0 because Block 0 has SUMFLG_SAME_OFFSET!)
# - Block 2: X (overwrites Block 2 on disk, expiring Block 2 once offset > 2*BLOCK)
# - Blocks 3..22: 20 blocks of A (must match surviving Block 0!)
src3.write_bytes(block_a + block_d + block_x + (block_a * N_TAIL))

proc3 = run_rsync('-a', '-I', '--inplace', '--no-whole-file',
                  f'--block-size={BLOCK}', '--no-compress',
                  '--stats', '--debug=deltasum1',
                  f'{src3}', f'{dst3}', capture_output=True)

_, _, literal3, matched3 = parse_stats(proc3.stdout + proc3.stderr)

expected_literal3 = 2 * BLOCK          # Only Block 1 (D) and Block 2 (X)
expected_matched3 = (1 + N_TAIL) * BLOCK  # Block 0 (A) + 20 trailing A blocks

if literal3 != expected_literal3 or matched3 != expected_matched3:
    test_fail(
        f"Case 3 (--inplace SUMFLG_SAME_OFFSET preservation) failed: "
        f"literal={literal3} (expected {expected_literal3}), "
        f"matched={matched3} (expected {expected_matched3})")

assert_same(dst3, src3, label='Case 3: --inplace SUMFLG_SAME_OFFSET preservation')

print(
    f"dup-chain test passed: forward_alarms={false_alarms1}, "
    f"interleaved_alarms={false_alarms2}, inplace_matched={matched3}"
)

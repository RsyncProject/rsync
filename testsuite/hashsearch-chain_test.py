#!/usr/bin/env python3
"""Regression test for issue #217: hash_search() must not blow up to O(N^2).

A disk/VM image can contain a huge number of blocks that share the same weak
rolling checksum (e.g. long runs of identical bytes).  All such blocks land on
a single hash-table chain.  When the sender then rolls across a region whose
weak checksum keeps landing on that chain but never produces a strong-checksum
match, the inner loop of hash_search() used to walk the *entire* chain for
every single byte offset.  The transfer would peg one CPU at 100% for hours
with no visible progress -- "rsync hangs at 100% CPU".

We reproduce a small, deterministic version of that pathology:

  * Destination/basis file: many identical "decoy" blocks.  Each decoy is an
    all-C block whose first three bytes are perturbed by (+1,-2,+1).  That
    perturbation leaves rsync's weak checksum (get_checksum1) unchanged but
    changes the strong checksum, so every decoy shares one weak checksum yet
    never strong-matches the source -> one very long chain, all false alarms.

  * Source file: a short run of the constant byte C.  Every window has that
    same weak checksum, so without a bound the sender walks the whole chain at
    every offset.

Rather than measure wall-clock time (which is hopelessly machine dependent),
we count the work directly.  rsync's existing `false_alarms` counter -- shown
by `--debug=deltasum1` -- is incremented once per strong-checksum comparison
that fails, i.e. exactly once per chain entry examined and rejected.  With the
per-offset cap in match.c the sender examines at most MAX_CHAIN_LEN entries per
hash hit, so false_alarms / hash_hits stays bounded no matter how long the
chain is; without the cap that ratio equals the full chain length.  We assert
the bound, which is the same integer on every machine.

The fix is sender-only (it changes no checksum, byte, or protocol field), so
the transferred result must also still be byte-for-byte correct.
"""

import re

from rsyncfns import (
    FROMDIR, TODIR, assert_same, rmtree, run_rsync, test_fail,
)

BLOCK = 256
NBLOCKS = 8000           # chain length -- much longer than match.c's cap (1024)
RUN = 3000               # length of the constant-byte region in the source
C = 100                  # the constant source byte

# A correct fix bounds the per-hash-hit chain walk to match.c's MAX_CHAIN_LEN
# (1024).  Pick an assertion bound comfortably between that cap and the full
# chain length so the test is insensitive to modest cap retuning but still
# fails hard for the unbounded (pre-fix) walk of NBLOCKS per hit.
MAX_FALSE_ALARMS_PER_HIT = NBLOCKS // 4   # 2000: > cap(1024), << chain(8000)


def make_decoy_block() -> bytes:
    b = bytearray([C]) * BLOCK
    # (+1,-2,+1) at positions 0,1,2 preserves get_checksum1 (both s1 and s2)
    # while changing the block's content and thus its strong checksum.
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

# Basis: NBLOCKS identical decoys -> one giant weak-checksum chain.
decoy = make_decoy_block()
with open(dst, 'wb') as f:
    for _ in range(NBLOCKS):
        f.write(decoy)

# Source: a constant-byte run (constant weak checksum, no strong match).
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

# Correctness is non-negotiable: the cap only skips matches, never data.
assert_same(dst, src, label='issue #217 chain-cap transfer')

print(f"issue #217: bounded at {ratio:.0f} false alarms/hit "
      f"(chain={NBLOCKS}, cap keeps it under {MAX_FALSE_ALARMS_PER_HIT})")

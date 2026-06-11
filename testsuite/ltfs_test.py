#!/usr/bin/env python3
# Test rsync's --ltfs mode (LTFS / tape-aware read ordering).
#
# On a real LTFS mount each file's starting data block is published as the
# "ltfs.startblock" virtual xattr; --ltfs reads files in ascending start-block
# order so the tape streams forward in one pass instead of seeking back and
# forth in name order.  We can't mount a tape here, so we stand in the
# "user.ltfs.startblock" alias (which the feature also honors) on an ordinary
# filesystem and assign blocks that run opposite to name order.  The generator
# processes files in its read order and -i emits one itemized line per file in
# that same order, so the itemized output is our observable proxy for the
# physical read schedule.

import os
import re

from rsyncfns import (
    FROMDIR, SCRATCHDIR, TODIR,
    checkit, makepath, run_rsync, test_fail, test_skipped,
)


# The feature needs a build with xattr support...
vv = run_rsync('-VV', check=True, capture_output=True)
if '"xattrs": true' not in vv.stdout:
    test_skipped("Rsync is configured without xattrs support")

# ...and a scratch filesystem that lets us set a user.* xattr to stand in for
# the tape's ltfs.startblock attribute.
makepath(FROMDIR)
probe = FROMDIR / '.xattr-probe'
probe.write_text('x')
try:
    os.setxattr(str(probe), 'user.ltfs.startblock', b'0')
except OSError as e:
    test_skipped(f"scratch filesystem does not support user xattrs: {e}")
probe.unlink()


def set_block(path, block):
    os.setxattr(str(path), 'user.ltfs.startblock', str(block).encode())


# --- 1. round-trip integrity -----------------------------------------------
# Five files whose start blocks run opposite to name order.
flat = {'alpha': 500, 'bravo': 400, 'charlie': 300, 'delta': 200, 'echo': 100}
for name, blk in flat.items():
    f = FROMDIR / f'{name}.dat'
    f.write_text(f'content of {name}\n')
    set_block(f, blk)

# --ltfs must produce a byte-identical destination tree.
checkit(['-r', '--ltfs', f'{FROMDIR}/', f'{TODIR}/'], FROMDIR, TODIR)


# --- 2. physical read order, across subdirectories -------------------------
# The itemized output (one line per file, in generator processing order) must
# come out in ascending start-block order regardless of name/directory order,
# and directories (no start block) must be handled before the bulk data read.
src2 = SCRATCHDIR / 'from2'
dst2 = SCRATCHDIR / 'to2'
makepath(src2 / 'asub', src2 / 'zsub')

layout = {
    'zsub/low.dat': 150,
    'zsub/mid.dat': 300,
    'top.dat': 600,
    'asub/high.dat': 900,
}
for rel, blk in layout.items():
    f = src2 / rel
    f.write_text(f'block {blk}\n')
    set_block(f, blk)

res = run_rsync('-r', '-i', '--ltfs', f'{src2}/', f'{dst2}/',
                check=True, capture_output=True)

# Pull the per-file itemized lines (a leading ">f"/"cf"/etc. transfer code)
# in the order rsync emitted them.
got = re.findall(r'^[<>ch.][fdLDS]\S*\s+(\S+\.dat)$', res.stdout, re.MULTILINE)
expected = [rel for rel, _ in sorted(layout.items(), key=lambda kv: kv[1])]
if got != expected:
    test_fail(f"--ltfs read order was {got}, expected tape order {expected}")

# And the content must still be correct.
checkit(['-r', '--ltfs', f'{src2}/', f'{dst2}/'], src2, dst2)


# --- 3. --checksum is refused ----------------------------------------------
# It would read every byte of every file off the tape just to decide what to
# transfer, defeating the point, so it must error rather than be honored.
res = run_rsync('-r', '--ltfs', '--checksum', f'{FROMDIR}/', f'{TODIR}/',
                check=False, capture_output=True)
if res.returncode == 0:
    test_fail("--ltfs --checksum was accepted; expected a usage error")
if 'checksum' not in (res.stderr + res.stdout):
    test_fail(f"--ltfs --checksum gave an unexpected error: {res.stderr!r}")

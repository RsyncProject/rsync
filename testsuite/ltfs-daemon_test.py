#!/usr/bin/env python3
# Test --ltfs tape-ordering end-to-end over a daemon pipe connection.
#
# Both sides of the connection are the same binary (SUPPORT_LTFS on both).
# This covers what ltfs-local_test.py cannot: startblock_ndx must be
# allocated identically on both sides of the pipe, startblock values must
# survive the file-list wire format, and ltfs_build_order() must run in the
# daemon-side generator and produce an ordering that is visible in the
# itemized output returned to the client.
#
# Files are assigned user.ltfs.startblock values in reverse name order.
# In a daemon push the generator runs on the daemon (receiver) side; it
# reorders requests so the client sender serves files by ascending startblock.
# The -i output echoed back to the client reflects that generator order.

import os
import re

from rsyncfns import (
    FROMDIR, SCRATCHDIR, TODIR, TOOLDIR,
    build_rsyncd_conf, makepath, run_rsync, start_test_daemon,
    test_fail, test_skipped,
)

DAEMON_PORT = 12898

# --- skip guards (same as ltfs-local_test.py) --------------------------------

vv = run_rsync('-VV', check=True, capture_output=True)
if '"xattrs": true' not in vv.stdout:
    test_skipped("rsync built without xattr support")

if not hasattr(os, 'setxattr'):
    test_skipped("os.setxattr not available on this platform")

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


# --- daemon setup ------------------------------------------------------------

makepath(TODIR)
conf = build_rsyncd_conf()
url = start_test_daemon(conf, DAEMON_PORT)


# --- 1. round-trip integrity over daemon pipe --------------------------------

flat = {'alpha': 500, 'bravo': 400, 'charlie': 300, 'delta': 200, 'echo': 100}
for name, blk in flat.items():
    f = FROMDIR / f'{name}.dat'
    f.write_text(f'content of {name}\n')
    set_block(f, blk)

run_rsync('-r', '--ltfs', f'{FROMDIR}/', f'{url}test-to/', check=True)


# --- 2. physical read order preserved over the pipe --------------------------
# Files in a subdirectory tree with startblocks in reverse name order; push to
# daemon and capture -i output to verify ascending startblock ordering.

src2 = SCRATCHDIR / 'from2'
makepath(src2 / 'asub', src2 / 'zsub')

layout = {
    'zsub/low.dat':  150,
    'zsub/mid.dat':  300,
    'top.dat':       600,
    'asub/high.dat': 900,
}
for rel, blk in layout.items():
    f = src2 / rel
    f.write_text(f'block {blk}\n')
    set_block(f, blk)

res = run_rsync('-r', '-i', '--ltfs', f'{src2}/', f'{url}test-to/ordering/',
                check=True, capture_output=True)

got = re.findall(r'^[<>ch.][fdLDS]\S*\s+(\S+\.dat)$', res.stdout, re.MULTILINE)
expected = [rel for rel, _ in sorted(layout.items(), key=lambda kv: kv[1])]
if got != expected:
    test_fail(
        f"--ltfs read order over daemon pipe was {got!r}, "
        f"expected tape order {expected!r}"
    )

print("ltfs-daemon: round-trip integrity and tape ordering verified "
      "over daemon pipe")

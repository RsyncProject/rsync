#!/usr/bin/env python3
# Regression test for issue #951.
#
# When rsync is built against a system zlib (no bundled Z_INSERT_ONLY
# extension), send_deflated_token() falls back to Z_SYNC_FLUSH to add a
# matched block to the compressor history -- but Z_SYNC_FLUSH emits a flush
# block into a fixed-size obuf.  A large incompressible matched block
# overflowed obuf and aborted the transfer with
#     "deflate on token returned 0 (N bytes left)"  at token.c
# The fix loops, discarding the (never-sent) output, until the input is
# consumed.  A bundled-zlib build emits no output here, so this test passes
# on either build; it is RED only on a pre-fix system-zlib build.
#
# The matched-block insert path needs all of: --compress-choice=zlib (the
# only method that feeds matched blocks into the deflate history), a large
# --block-size so a single matched token exceeds obuf, incompressible
# (random) data, and a delta over a real connection (compression is skipped
# for purely local transfers).  We assert the upload SUCCEEDS *and* the
# result matches the source, so the fix is verified correct, not merely
# non-crashing.

import filecmp
import shutil
import subprocess

from rsyncfns import (
    SCRATCHDIR, make_data_file, makepath, rmtree, rsync_argv,
    start_test_daemon, test_fail, write_daemon_conf,
)

DAEMON_PORT = 12922
SIZE = 8 * 1024 * 1024     # enough blocks to exercise many inserts
# 65535 (0xffff) is a single insert fragment larger than the deflate output
# buffer (AVAIL_OUT_SIZE(CHUNK_SIZE) ~= 32816).  It exercises both failure
# modes of the pre-fix code: the obuf overflow abort, and -- once that is
# loop-drained -- pending insert output left in the stream that leaks into
# the next send.  A block that splits into chunks ending in a tiny fragment
# (e.g. 131072 = 65535+65535+2) would hide the pending case.
BLOCK = 65535

moddir = SCRATCHDIR / 'zmod'
srcdir = SCRATCHDIR / 'zsrc'
rmtree(moddir)
rmtree(srcdir)
makepath(moddir)
makepath(srcdir)

# Source is incompressible.  The basis (already in the module) is the same
# data with a few bytes changed in one block, so every other 128KB block
# matches exactly and is sent as a token -> the deflate insert path.
make_data_file(srcdir / 'big.dat', SIZE)
shutil.copy(srcdir / 'big.dat', moddir / 'big.dat')
with open(srcdir / 'big.dat', 'r+b') as f:
    f.seek(SIZE // 2 + 1000)
    f.write(b'\x00' * 32)

conf = write_daemon_conf([('zmod', {'path': str(moddir), 'read only': 'no'})])
url = start_test_daemon(conf, DAEMON_PORT) + 'zmod/'

# -I forces the delta even though the basis has the same size (otherwise the
# quick check skips the file and the matched-block insert path never runs).
proc = subprocess.run(
    rsync_argv('-zI', '--compress-choice=zlib', '--no-whole-file',
               f'--block-size={BLOCK}', str(srcdir / 'big.dat'), url),
    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
if proc.returncode != 0:
    print(proc.stdout)
    test_fail(f"zlib delta upload failed (rc={proc.returncode}); "
              "regression of #951 deflate-token overflow")

if not filecmp.cmp(srcdir / 'big.dat', moddir / 'big.dat', shallow=False):
    test_fail("uploaded file differs from source -- zlib delta corruption")

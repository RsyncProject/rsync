#!/usr/bin/env python3
"""Regression test for --sparse write amplification (issue #773).

write_file()'s sparse path used to hand the data to write_sparse() in
SPARSE_WRITE_SIZE (1024-byte) slices, and write_sparse() issued a separate
write() syscall for each slice.  Copying a large *non-sparse* file with
--sparse therefore cost roughly one write() per kilobyte -- e.g. ~1,000,000
write() calls for a 1 GiB file -- which on real storage ran 100x+ slower than
the same copy without --sparse (1.36 MB/s vs 391 MB/s in the bug report).

write_sparse() now scans each span for hole-worthy zero runs itself and emits
each non-zero region with a single write(), so the syscall count tracks the
data's natural chunking instead of its size in kilobytes.

We copy a 16 MiB random (hole-free) file with --sparse under strace and assert
the number of write() syscalls is far below the old size/1024 behaviour.  The
test is skipped where strace is unavailable or non-functional (e.g. non-Linux
or a sandbox that blocks ptrace).
"""

import os
import shutil
import subprocess

from rsyncfns import (
    FROMDIR, TODIR, rmtree, rsync_argv, test_fail, test_skipped,
)

FILESIZE = 16 * 1024 * 1024
# Old code: FILESIZE/1024 ~= 16384 write()s.  New code: a few hundred.
# Anything below FILESIZE/8192 (~2048) cleanly separates the two.
MAX_WRITES = FILESIZE // 8192

strace = shutil.which('strace')
if not strace:
    test_skipped("strace not available")

rmtree(FROMDIR)
rmtree(TODIR)
FROMDIR.mkdir(parents=True, exist_ok=True)
TODIR.mkdir(parents=True, exist_ok=True)

data = os.urandom(FILESIZE)  # random => no zero runs => maximal write pressure
(FROMDIR / 'big').write_bytes(data)

argv = [strace, '-f', '-e', 'trace=write', '-o', '/dev/stdout',
        *rsync_argv('-a', '--sparse', f'{FROMDIR}/', f'{TODIR}/')]
proc = subprocess.run(argv, capture_output=True, text=True)

# Make sure strace itself worked (some sandboxes deny ptrace).
if proc.returncode != 0 and 'write(' not in proc.stdout:
    test_skipped(f"strace could not trace rsync: {proc.stderr.strip()[:200]}")

writes = proc.stdout.count('write(')

if not (TODIR / 'big').is_file() or (TODIR / 'big').read_bytes() != data:
    test_fail("--sparse copy produced wrong file contents")

if writes > MAX_WRITES:
    test_fail(f"--sparse made {writes} write() syscalls for a {FILESIZE}-byte "
              f"file (> {MAX_WRITES}); write_sparse() is dribbling out "
              f"SPARSE_WRITE_SIZE chunks (issue #773 regression)")

print(f"OK: --sparse copy used {writes} write() syscalls (<= {MAX_WRITES})")

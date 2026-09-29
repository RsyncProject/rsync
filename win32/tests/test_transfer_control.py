#!/usr/bin/env python3
"""--partial-dir, --temp-dir, --delay-updates, --update and --sparse.

These share a subject: where the receiver puts a file while it is being
written, and when it moves into place.  On Windows that matters more than it
looks, because a rename over an open file behaves differently here and the
receiver half runs as a thread rather than a forked process.

The upstream partial test interrupts a transfer with a signal to produce a
partial file.  That does not carry over -- TerminateProcess gives rsync no
chance to run the cleanup handler that moves the partial into place -- so
the resume path is exercised from the other end instead, by planting a
partial and checking the generator picks it up as the basis.

Copyright (C) 2026 Max Vilimpoc, rsync CMake/Windows port.
Distributed under the same GPL-3.0-or-later terms as the rest of rsync.
"""

import os

from wintest import (SCRATCH, check, compare_trees, ok, pseudo_random, rewrite,
                     rsync, write)

# --- --temp-dir ------------------------------------------------------------
src = SCRATCH / 'from'
write(src / 'top.txt', 'top\n')
write(src / 'a' / 'one.txt', 'one\n')
write(src / 'a' / 'b' / 'deep.bin', pseudo_random(120000, seed=3))

tmpdir = SCRATCH / 'scratch-temp'      # outside both from/ and to/
tmpdir.mkdir(parents=True, exist_ok=True)
dst = SCRATCH / 'to-temp'
rsync('-r', f'--temp-dir={tmpdir}', f'{src}/', f'{dst}/')
compare_trees(src, dst, '--temp-dir transfer')
check(not any(tmpdir.iterdir()), '--temp-dir left scratch files behind')
for dirpath, _dirnames, filenames in os.walk(dst):
    for name in filenames:
        check(not name.startswith('.') or not name.endswith('~'),
              f'a temp file was left in the destination tree: {name}')

# A --temp-dir that does not exist must be an error, which is what shows the
# option is consulted rather than quietly ignored.
proc = rsync('-r', f'--temp-dir={SCRATCH}/no-such-dir', f'{src}/',
             f'{SCRATCH}/to-temp-bad/', expect=None)
check(proc.returncode != 0, 'a nonexistent --temp-dir was accepted')

# --- --partial-dir as a resume basis --------------------------------------
# Plant a truncated prefix where --partial-dir would have left one, and the
# generator should adopt it rather than starting the file from nothing.
big = pseudo_random(200000, seed=7)
psrc = SCRATCH / 'psrc'
write(psrc / 'big.bin', big)

partial_dir = SCRATCH / 'partials'
partial_dir.mkdir(parents=True, exist_ok=True)
write(partial_dir / 'big.bin', big[:120000])

pdst = SCRATCH / 'to-partial'
proc = rsync('-r', '-i', '--no-whole-file', f'--partial-dir={partial_dir}',
             f'{psrc}/', f'{pdst}/')
check((pdst / 'big.bin').read_bytes() == big,
      '--partial-dir resume produced the wrong content')
check(not (partial_dir / 'big.bin').exists(),
      '--partial-dir did not consume the partial file it used')

# A completed transfer leaves no partial behind.
partial_dir2 = SCRATCH / 'partials2'
pdst2 = SCRATCH / 'to-partial2'
rsync('-r', '--partial-dir=' + str(partial_dir2), f'{psrc}/', f'{pdst2}/')
compare_trees(psrc, pdst2, '--partial-dir transfer')
check(not partial_dir2.exists() or not any(partial_dir2.iterdir()),
      '--partial-dir kept a partial after a successful transfer')

# A relative --partial-dir is created inside the destination and removed
# again; nothing of it should survive a clean run.
pdst3 = SCRATCH / 'to-partial3'
rsync('-r', '--partial-dir=.rsync-partial', f'{psrc}/', f'{pdst3}/')
check(not (pdst3 / '.rsync-partial').exists(),
      'a relative --partial-dir was left in the destination')

# --- --delay-updates -------------------------------------------------------
dsrc = SCRATCH / 'dsrc'
ddst = SCRATCH / 'ddst'
write(dsrc / 'foo.txt', '1\n')
rsync('-r', '-i', '--delay-updates', f'{dsrc}/', f'{ddst}/')
check((ddst / 'foo.txt').read_text() == '1\n',
      '--delay-updates did not install the file')
check(not (ddst / '.~tmp~').exists(),
      '--delay-updates left its staging directory behind')

# A stale staging directory from an earlier interrupted run must not confuse
# the next one.
(ddst / '.~tmp~').mkdir(exist_ok=True)
write(ddst / '.~tmp~' / 'foo.txt', '2\n')
# rewrite(), not write(): '1\n' and '3\n' are the same size, so a rewrite
# inside the same second looks unchanged to the quick check and is skipped.
rewrite(dsrc / 'foo.txt', '3\n')
rsync('-r', '-i', '--delay-updates', f'{dsrc}/', f'{ddst}/')
check((ddst / 'foo.txt').read_text() == '3\n',
      f'--delay-updates over a stale staging dir gave '
      f'{(ddst / "foo.txt").read_text()!r}')
check(not (ddst / '.~tmp~').exists(),
      '--delay-updates left its staging directory behind on the second run')

# --- --update skips a newer destination file ------------------------------
usrc = SCRATCH / 'usrc'
udst = SCRATCH / 'udst'
write(usrc / 'older.txt', 'from source\n', mtime=1_600_000_000)
write(udst / 'older.txt', 'newer at destination\n', mtime=1_700_000_000)
write(usrc / 'newer.txt', 'from source\n', mtime=1_700_000_000)
write(udst / 'newer.txt', 'older at destination\n', mtime=1_600_000_000)

rsync('-rt', '--update', f'{usrc}/', f'{udst}/')
check((udst / 'older.txt').read_text() == 'newer at destination\n',
      '--update overwrote a destination file that was newer')
check((udst / 'newer.txt').read_text() == 'from source\n',
      '--update failed to replace a destination file that was older')

# --- --sparse --------------------------------------------------------------
# Windows has no fallocate here, so --sparse means seeking over zero runs.
# What must hold is that the file comes out byte-for-byte correct.
ssrc = SCRATCH / 'ssrc'
sdst = SCRATCH / 'sdst'
holey = pseudo_random(4096, seed=11) + bytes(300000) + pseudo_random(4096, seed=12)
write(ssrc / 'holey.bin', holey)
rsync('-r', '--sparse', f'{ssrc}/', f'{sdst}/')
check((sdst / 'holey.bin').read_bytes() == holey,
      '--sparse did not reproduce the file byte-for-byte')

ok('--temp-dir, --partial-dir, --delay-updates, --update and --sparse correct')

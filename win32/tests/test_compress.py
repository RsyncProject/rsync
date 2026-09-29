#!/usr/bin/env python3
"""Compression: --compress-choice, --compress-level and --skip-compress.

test_options.py checks that -z round-trips.  This covers the negotiation and
the option parsing around it -- which matters here because this build links
the bundled zlib and advertises "zlibx zlib none", so asking for a codec it
does not have must fail cleanly rather than fall over.

Copyright (C) 2026 Max Vilimpoc, rsync CMake/Windows port.
Distributed under the same GPL-3.0-or-later terms as the rest of rsync.
"""

from wintest import (SCRATCH, check, compare_trees, ok, pseudo_random, rsync,
                     write)

src = SCRATCH / 'from'
# Compressible text, incompressible noise, and a name in the default
# --skip-compress list so that path is taken too.
write(src / 'text.txt', 'the same line over and over\n' * 5000)
write(src / 'noise.bin', pseudo_random(200000, seed=23))
write(src / 'already.gz', pseudo_random(80000, seed=29))
write(src / 'sub' / 'more.txt', 'nested compressible text\n' * 2000)

# --- each advertised codec round-trips ------------------------------------
for choice in ('zlib', 'zlibx', 'none'):
    dst = SCRATCH / f'to-{choice}'
    rsync('-rt', '-z', f'--compress-choice={choice}', f'{src}/', f'{dst}/')
    compare_trees(src, dst, f'--compress-choice={choice}')

# --- an unavailable codec is refused, not ignored -------------------------
proc = rsync('-rt', '-z', '--compress-choice=zstd', f'{src}/',
             f'{SCRATCH}/to-zstd/', expect=None)
check(proc.returncode != 0,
      'a compression method this build lacks was accepted')
combined = proc.stdout + proc.stderr
check('zstd' in combined,
      f'the rejection did not name the method asked for:\n{combined}')

# --- --compress-level ------------------------------------------------------
for level in (1, 6, 9):
    dst = SCRATCH / f'to-level{level}'
    rsync('-rt', '-z', f'--compress-level={level}', f'{src}/', f'{dst}/')
    compare_trees(src, dst, f'--compress-level={level}')

# --compress-level=0 means no compression even with -z given.
dst = SCRATCH / 'to-level0'
rsync('-rt', '-z', '--compress-level=0', f'{src}/', f'{dst}/')
compare_trees(src, dst, '--compress-level=0')

# --- --skip-compress -------------------------------------------------------
# The suffix list changes what gets compressed, not what arrives; the tree
# has to be identical either way.
dst = SCRATCH / 'to-skip'
rsync('-rt', '-z', '--skip-compress=txt/bin', f'{src}/', f'{dst}/')
compare_trees(src, dst, '--skip-compress')

dst = SCRATCH / 'to-skip-empty'
rsync('-rt', '-z', '--skip-compress=', f'{src}/', f'{dst}/')
compare_trees(src, dst, '--skip-compress= (compress everything)')

# --- -z with a delta rather than whole files ------------------------------
# Compression and the delta algorithm have to cooperate: the literal runs in
# the token stream are compressed, the matched blocks are not sent at all.
body = pseudo_random(300000, seed=31)
dsrc = SCRATCH / 'dsrc'
ddst = SCRATCH / 'ddst'
write(dsrc / 'doc.bin', body + b'-NEW-TAIL', mtime=1_600_000_000)
write(ddst / 'doc.bin', body + b'-OLD-TAIL', mtime=1_500_000_000)
rsync('-rt', '-z', '--no-whole-file', f'{dsrc}/', f'{ddst}/')
check((ddst / 'doc.bin').read_bytes() == body + b'-NEW-TAIL',
      'a compressed delta transfer produced the wrong content')

ok('compression choices, levels, --skip-compress and compressed deltas correct')

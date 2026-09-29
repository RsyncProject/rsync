#!/usr/bin/env python3
"""--inplace and --append, which need ftruncate().

The CRT spells it _chsize_s; without wiring that up rsync reports
"no inplace, no append" in --version and refuses both options.

Copyright (C) 2026 Max Vilimpoc, rsync CMake/Windows port.
Distributed under the same GPL-3.0-or-later terms as the rest of rsync.
"""

from wintest import (FROM, TO, check, have_capability, ok, pseudo_random,
                     rewrite, rsync, skip, write)

if not have_capability('inplace'):
    skip('rsync was built without ftruncate(), so --inplace is unavailable')

blob = FROM / 'blob.bin'
body = pseudo_random(200000, seed=3)
write(blob, body)
rsync('-rt', f'{FROM}/', f'{TO}/')

# --inplace: edit in the middle, rewrite the destination without a temp file.
edited = bytearray(body)
for i in range(90000, 90200):
    edited[i] ^= 0x77
rewrite(blob, bytes(edited))
rsync('-rt', '--inplace', '--no-whole-file', f'{FROM}/', f'{TO}/')
check((TO / 'blob.bin').read_bytes() == bytes(edited),
      '--inplace produced the wrong content')

# --inplace must also shrink a file correctly, which is the ftruncate path.
shorter = bytes(edited)[:50000]
rewrite(blob, shorter)
rsync('-rt', '--inplace', '--no-whole-file', f'{FROM}/', f'{TO}/')
check((TO / 'blob.bin').read_bytes() == shorter,
      '--inplace did not truncate the destination when the source shrank')

# --append: the destination is a prefix of the source, so only the tail moves.
# A local copy defaults to --whole-file, which --append refuses, so ask for
# the delta path explicitly.
base = pseudo_random(80000, seed=11)
write(FROM / 'grow.bin', base)
rsync('-rt', f'{FROM}/', f'{TO}/')

grown = base + pseudo_random(40000, seed=12)
rewrite(FROM / 'grow.bin', grown)
proc = rsync('-rt', '--append', '--no-whole-file', '--stats',
             f'{FROM}/', f'{TO}/')
check((TO / 'grow.bin').read_bytes() == grown,
      '--append produced the wrong content')

for line in proc.stdout.splitlines():
    if line.startswith('Literal data:'):
        literal = int(line.split()[2].replace(',', ''))
        check(literal <= 60000,
              f'--append sent {literal} literal bytes for a 40000-byte tail')
        print(f'--append sent {literal} literal bytes')
        break

ok('--inplace (including truncation) and --append both correct')

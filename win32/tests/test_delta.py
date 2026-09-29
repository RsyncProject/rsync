#!/usr/bin/env python3
"""The delta algorithm over the local-server pipe.

A local copy defaults to --whole-file (there is no network to save), so
--no-whole-file is needed to make rsync actually compute deltas.  The test
then checks that changing a small part of a large file moves far less than
the whole file, and that the result is still byte-exact.

Copyright (C) 2026 Max Vilimpoc, rsync CMake/Windows port.
Distributed under the same GPL-3.0-or-later terms as the rest of rsync.
"""

import hashlib

from wintest import (FROM, TO, check, ok, pseudo_random, rewrite, rsync,
                     write)

SIZE = 400000
blob = FROM / 'blob.bin'

original = pseudo_random(SIZE, seed=7)
write(blob, original)
rsync('-rt', f'{FROM}/', f'{TO}/')
check((TO / 'blob.bin').read_bytes() == original, 'initial copy differs')

# Change 100 bytes in the middle; the rest should be matched, not resent.
edited = bytearray(original)
for i in range(200000, 200100):
    edited[i] ^= 0x5A
rewrite(blob, bytes(edited))

proc = rsync('-rt', '--no-whole-file', '--stats', f'{FROM}/', f'{TO}/')

check((TO / 'blob.bin').read_bytes() == bytes(edited),
      'file content differs after the delta transfer')

literal = matched = None
for line in proc.stdout.splitlines():
    if line.startswith('Literal data:'):
        literal = int(line.split()[2].replace(',', ''))
    elif line.startswith('Matched data:'):
        matched = int(line.split()[2].replace(',', ''))

check(literal is not None and matched is not None,
      '--stats did not report literal/matched data')
print(f'literal={literal} matched={matched} of {SIZE}')

check(matched > SIZE // 2,
      f'delta matched only {matched} of {SIZE} bytes; block matching looks broken')
check(literal < SIZE // 2,
      f'delta sent {literal} literal bytes for a 100-byte edit')

# And the whole-file path must still produce the right bytes.
for i in range(0, 5000):
    edited[i] ^= 0x33
rewrite(blob, bytes(edited))
rsync('-rt', '--whole-file', f'{FROM}/', f'{TO}/')
check(hashlib.md5((TO / 'blob.bin').read_bytes()).hexdigest()
      == hashlib.md5(bytes(edited)).hexdigest(),
      'whole-file transfer produced the wrong content')

ok('delta transfer matched unchanged blocks and stayed byte-exact')

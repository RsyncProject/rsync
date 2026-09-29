#!/usr/bin/env python3
"""--fuzzy, and the delta path it feeds.

A final tree match proves nothing on its own -- a plain full transfer
produces exactly the same tree -- so the upstream fuzzy test asserts through
--debug=FUZZY that the generator really picked the renamed file as the
basis.  That does not carry over: for a local copy this port re-execs itself
as --server rather than forking, and server_options() forwards --info but
not --debug, so a level a forked generator would have inherited in memory
never reaches ours.  The feature works; only the diagnostic is missing.

--stats says the same thing more directly, and is the stronger assertion of
the two: matched-versus-literal bytes show the basis was not merely found
but actually used for block matching.

Copyright (C) 2026 Max Vilimpoc, rsync CMake/Windows port.
Distributed under the same GPL-3.0-or-later terms as the rest of rsync.
"""

import re

from wintest import (SCRATCH, check, compare_trees, ok, pseudo_random, rsync,
                     write)


def transfer_stats(proc):
    """(matched, literal) bytes from an rsync --stats run."""
    def field(label):
        m = re.search(rf'{label}:\s+([\d,]+) bytes', proc.stdout)
        check(m is not None, f'--stats did not report "{label}":\n{proc.stdout}')
        return int(m.group(1).replace(',', ''))
    return field('Matched data'), field('Literal data')


body = pseudo_random(300000, seed=13)

# --- the basic case: the same content under a different name --------------
src = SCRATCH / 'from'
dst = SCRATCH / 'to'
write(src / 'report.bin', body + b'-NEW-TAIL', mtime=1_600_000_000)
# The destination already holds nearly the same bytes, under the old name.
write(dst / 'report-old.bin', body + b'-OLD-TAIL', mtime=1_500_000_000)

proc = rsync('-rt', '--no-whole-file', '--fuzzy', '--stats',
             f'{src}/', f'{dst}/')
matched, literal = transfer_stats(proc)
check(matched > 250000,
      f'--fuzzy did not use report-old.bin as a basis: only {matched} bytes '
      f'matched, {literal} literal')
check((dst / 'report.bin').read_bytes() == body + b'-NEW-TAIL',
      '--fuzzy produced the wrong content')

# --- the negative control -------------------------------------------------
# Identical setup without --fuzzy must send the whole file.  Without this the
# assertion above could be satisfied by any basis-finding mechanism at all.
src2 = SCRATCH / 'from2'
dst2 = SCRATCH / 'to2'
write(src2 / 'report.bin', body + b'-NEW-TAIL', mtime=1_600_000_000)
write(dst2 / 'report-old.bin', body + b'-OLD-TAIL', mtime=1_500_000_000)

proc = rsync('-rt', '--no-whole-file', '--stats', f'{src2}/', f'{dst2}/')
matched, literal = transfer_stats(proc)
check(matched == 0,
      f'a basis was used without --fuzzy: {matched} bytes matched')
check(literal >= 300000,
      f'without --fuzzy the whole file should be literal, got {literal}')

# --- --fuzzy with --delete-delay removes the stale basis afterwards -------
src3 = SCRATCH / 'from3'
dst3 = SCRATCH / 'to3'
write(src3 / 'doc.bin', body + b'-V2', mtime=1_600_000_000)
write(dst3 / 'doc-v1.bin', body + b'-V1', mtime=1_500_000_000)

rsync('-rt', '--no-whole-file', '--fuzzy', '--delete-delay',
      f'{src3}/', f'{dst3}/')
compare_trees(src3, dst3, '--fuzzy with --delete-delay')

# --- the delta path itself: a basis full of repeated blocks ---------------
# hashsearch-chain territory: most blocks match, so the rolling checksum has
# to chain through repeated hash hits rather than settle on the first.
src4 = SCRATCH / 'from4'
dst4 = SCRATCH / 'to4'
block = pseudo_random(700, seed=21)
original = block * 400
modified = bytearray(original)
for offset in range(0, len(modified), 9999):
    modified[offset] = (modified[offset] + 1) & 0xFF
write(dst4 / 'chain.bin', bytes(original), mtime=1_500_000_000)
write(src4 / 'chain.bin', bytes(modified), mtime=1_600_000_000)
proc = rsync('-rt', '--no-whole-file', '--stats', f'{src4}/', f'{dst4}/')
check((dst4 / 'chain.bin').read_bytes() == bytes(modified),
      'the delta rebuild through a repetitive basis produced wrong content')
matched, literal = transfer_stats(proc)
check(matched > 0,
      f'the delta algorithm matched nothing against a near-identical basis '
      f'({literal} bytes literal)')

ok('--fuzzy finds and uses the renamed basis, and the delta path handles '
   'repetition')

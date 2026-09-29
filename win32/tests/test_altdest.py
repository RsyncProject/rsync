"""--compare-dest and --copy-dest.

The upstream alt-dest test covers these three together; --link-dest is left
out here because this build has no hard links, so it would have nothing to
link with.  The other two are meaningful: --compare-dest says "if the file
already matches the copy over there, do not send it and do not create it",
and --copy-dest says "copy it locally from over there instead of pulling it
across".

Copyright (C) 2026 Max Vilimpoc, rsync CMake/Windows port.
Distributed under the same GPL-3.0-or-later terms as the rest of rsync.
"""

from wintest import (SCRATCH, check, have_capability, ok, pseudo_random, rsync,
                     write)

src = SCRATCH / 'from'
write(src / 'same.txt', 'identical everywhere\n', mtime=1_600_000_000)
write(src / 'changed.txt', 'source version\n', mtime=1_600_000_000)
write(src / 'sub' / 'nested.bin', pseudo_random(60000, seed=5),
      mtime=1_600_000_000)

# The alternate destination holds an exact match for same.txt and a stale
# copy of changed.txt.
alt = SCRATCH / 'alt'
write(alt / 'same.txt', 'identical everywhere\n', mtime=1_600_000_000)
write(alt / 'changed.txt', 'stale version\n', mtime=1_500_000_000)

# --- --compare-dest --------------------------------------------------------
dst = SCRATCH / 'to-compare'
proc = rsync('-rt', '-i', f'--compare-dest={alt}', f'{src}/', f'{dst}/')
check(not (dst / 'same.txt').exists(),
      '--compare-dest created a file that already matched in the alt dir')
check((dst / 'changed.txt').is_file(),
      '--compare-dest did not transfer the file that differed')
check((dst / 'changed.txt').read_text() == 'source version\n',
      '--compare-dest delivered the wrong content for the changed file')
check((dst / 'sub' / 'nested.bin').is_file(),
      '--compare-dest did not transfer a file absent from the alt dir')

# --- --copy-dest -----------------------------------------------------------
# Same decision, different outcome: the matching file is created at the
# destination by copying it locally from the alt dir.
dst = SCRATCH / 'to-copy'
rsync('-rt', '-i', f'--copy-dest={alt}', f'{src}/', f'{dst}/')
check((dst / 'same.txt').is_file(),
      '--copy-dest did not create the matching file at the destination')
check((dst / 'same.txt').read_text() == 'identical everywhere\n',
      '--copy-dest produced the wrong content')
check((dst / 'changed.txt').read_text() == 'source version\n',
      '--copy-dest did not transfer the file that differed')
check((dst / 'sub' / 'nested.bin').read_bytes() ==
      (src / 'sub' / 'nested.bin').read_bytes(),
      '--copy-dest corrupted a file that was not in the alt dir')

# --- --copy-dest uses the alt file as a delta basis -----------------------
# changed.txt exists in the alt dir with different content, so with
# --no-whole-file the receiver should rebuild it from that basis rather than
# take the whole file.  -ii names the basis it settled on.
basis_src = SCRATCH / 'bsrc'
basis_alt = SCRATCH / 'balt'
body = pseudo_random(200000, seed=9)
write(basis_src / 'doc.bin', body + b'TAIL-NEW', mtime=1_600_000_000)
write(basis_alt / 'doc.bin', body + b'TAIL-OLD', mtime=1_500_000_000)

dst = SCRATCH / 'to-basis'
proc = rsync('-rt', '-ii', '--no-whole-file', f'--copy-dest={basis_alt}',
             f'{basis_src}/', f'{dst}/')
check((dst / 'doc.bin').read_bytes() == body + b'TAIL-NEW',
      '--copy-dest delta rebuild produced the wrong content')

# --- --compare-dest with --delete -----------------------------------------
# A destination file whose match lives only in the alt dir is still not the
# source's business; --delete must not remove what the source does not name.
dst = SCRATCH / 'to-del'
rsync('-rt', f'{src}/', f'{dst}/')
write(dst / 'extra.txt', 'only at the destination\n')
rsync('-rt', '--delete', f'--compare-dest={alt}', f'{src}/', f'{dst}/')
check(not (dst / 'extra.txt').exists(),
      '--delete with --compare-dest left an extraneous file behind')

if have_capability('hardlinks'):
    ok('--compare-dest and --copy-dest correct (note: hardlinks now available, '
       '--link-dest deserves coverage too)')
ok('--compare-dest and --copy-dest behave correctly')

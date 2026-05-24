#!/usr/bin/env python3
"""Breadth coverage of the algorithm-selection options at depth:
--compress-choice / --compress-level / --skip-compress and
--checksum-choice / --checksum-seed.

Compression and checksum choice don't change the result, so each available
algorithm is exercised for a clean, byte-identical transfer of a >=3-deep tree
(proving the option parses, negotiates and doesn't corrupt data).
"""

import json
import re

from rsyncfns import (
    FROMDIR, TODIR,
    assert_same, make_tree, rmtree, run_rsync, test_fail, walk_files,
)

src = FROMDIR
vv = json.loads(run_rsync('-VV', check=True, capture_output=True).stdout)
compressors = [a for a in vv.get('compress_list', []) if a != 'none']
checksums = [a for a in vv.get('checksum_list', []) if a != 'none']


def fresh():
    rmtree(src)
    rmtree(TODIR)
    make_tree(src, depth=3, data=True, data_size=4096)
    return [p.relative_to(src) for p in walk_files(src)]


def verify(rels, label):
    for rel in rels:
        assert_same(TODIR / rel, src / rel, label=f'{label} {rel}')


# --- --compress-choice for every advertised compressor ----------------------
# Byte-identical output alone proves only that the option didn't corrupt data;
# assert via --debug=NSTR (compat.c) that the requested compressor was actually
# selected for the transfer. The trailing " (level" anchors so zlib != zlibx.
for algo in compressors:
    rels = fresh()
    proc = run_rsync('-az', f'--compress-choice={algo}', '--debug=NSTR',
                     f'{src}/', f'{TODIR}/', capture_output=True)
    if not re.search(rf'compress: {re.escape(algo)} \(level', proc.stdout):
        test_fail(f"--compress-choice={algo} was not the selected compressor; "
                  f"--debug=NSTR output:\n{proc.stdout}")
    verify(rels, f'--compress-choice={algo}')

# --- --compress-level (the requested level reaches the compressor) ----------
rels = fresh()
proc = run_rsync('-az', '--compress-level=9', '--debug=NSTR',
                 f'{src}/', f'{TODIR}/', capture_output=True)
if not re.search(r'compress: \S+ \(level 9\)', proc.stdout):
    test_fail("--compress-level=9 was not applied; "
              f"--debug=NSTR output:\n{proc.stdout}")
verify(rels, '--compress-level=9')

# --- --skip-compress (the file must still arrive intact) --------------------
rels = fresh()
(src / 'd1' / 'd2' / 'x.gz').write_bytes(b'\x1f\x8b' + b'pseudo gzip body ' * 64)
run_rsync('-az', '--skip-compress=gz', f'{src}/', f'{TODIR}/')
assert_same(TODIR / 'd1' / 'd2' / 'x.gz', src / 'd1' / 'd2' / 'x.gz',
            label='--skip-compress gz')

# --- --checksum-choice for every advertised checksum ------------------------
# As above: assert via --debug=NSTR (checksum.c) that the requested checksum was
# the one negotiated, not merely that the transfer succeeded.
for algo in checksums:
    rels = fresh()
    proc = run_rsync('-a', '-c', f'--checksum-choice={algo}', '--debug=NSTR',
                     f'{src}/', f'{TODIR}/', capture_output=True)
    if not re.search(rf'checksum: {re.escape(algo)}\b', proc.stdout):
        test_fail(f"--checksum-choice={algo} was not the selected checksum; "
                  f"--debug=NSTR output:\n{proc.stdout}")
    verify(rels, f'--checksum-choice={algo}')

# --- --checksum-seed --------------------------------------------------------
rels = fresh()
run_rsync('-a', '-c', '--checksum-seed=12345', f'{src}/', f'{TODIR}/')
verify(rels, '--checksum-seed')

print("compress-options: compress/checksum algorithm selection verified at depth")

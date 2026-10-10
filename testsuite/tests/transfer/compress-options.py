#!/usr/bin/env python3

import json
import re

from harness.rsync import (
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

for algo in compressors:
    rels = fresh()
    proc = run_rsync('-az', f'--compress-choice={algo}', '--debug=NSTR',
                     f'{src}/', f'{TODIR}/', capture_output=True)
    if not re.search(rf'compress: {re.escape(algo)} \(level', proc.stdout):
        test_fail(f"--compress-choice={algo} was not the selected compressor; "
                  f"--debug=NSTR output:\n{proc.stdout}")
    verify(rels, f'--compress-choice={algo}')

rels = fresh()
level_algo = next((a for a in ('zlibx', 'zlib') if a in compressors), None)
level_args = [f'--compress-choice={level_algo}'] if level_algo else []
proc = run_rsync('-az', *level_args, '--compress-level=9', '--debug=NSTR',
                 f'{src}/', f'{TODIR}/', capture_output=True)
if not re.search(r'compress: \S+ \(level 9\)', proc.stdout):
    test_fail("--compress-level=9 was not applied; "
              f"--debug=NSTR output:\n{proc.stdout}")
verify(rels, '--compress-level=9')

rels = fresh()
(src / 'd1' / 'd2' / 'x.gz').write_bytes(b'\x1f\x8b' + b'pseudo gzip body ' * 64)
run_rsync('-az', '--skip-compress=gz', f'{src}/', f'{TODIR}/')
assert_same(TODIR / 'd1' / 'd2' / 'x.gz', src / 'd1' / 'd2' / 'x.gz',
            label='--skip-compress gz')

for algo in checksums:
    rels = fresh()
    proc = run_rsync('-a', '-c', f'--checksum-choice={algo}', '--debug=NSTR',
                     f'{src}/', f'{TODIR}/', capture_output=True)
    if not re.search(rf'checksum: {re.escape(algo)}\b', proc.stdout):
        test_fail(f"--checksum-choice={algo} was not the selected checksum; "
                  f"--debug=NSTR output:\n{proc.stdout}")
    verify(rels, f'--checksum-choice={algo}')

rels = fresh()
run_rsync('-a', '-c', '--checksum-seed=12345', f'{src}/', f'{TODIR}/')
verify(rels, '--checksum-seed')

print("compress-options: compress/checksum algorithm selection verified at depth")

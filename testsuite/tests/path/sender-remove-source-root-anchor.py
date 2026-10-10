#!/usr/bin/env python3

import os
import subprocess
from pathlib import Path

from harness.rsync import (
    SCRATCHDIR, makepath, rmtree, rsync_argv, test_fail, test_skipped,
)

if os.geteuid() != 0:
    test_skipped('needs root to place a transfer source directly under /', capability='root_anchor')

SOURCE_DATA = b'REAL-SOURCE-CONTENT'
DECOY_DATA = b'CWD-DECOY-CONTENT!!'
if len(SOURCE_DATA) != len(DECOY_DATA):
    test_fail('source and decoy fixtures must have equal lengths')

base = SCRATCHDIR / 'sender-remove-root-anchor'
rmtree(base)
dest = base / 'dst'
makepath(dest)

root_src = Path('/') / f'rsync-root-anchor-probe-{os.getpid()}'
decoy = base / root_src.name

try:
    try:
        root_src.write_bytes(SOURCE_DATA)
    except OSError as e:
        test_skipped(f'cannot create a transfer source under /: {e}', capability='root_anchor')
    decoy.write_bytes(DECOY_DATA)
    st = root_src.stat()
    os.utime(decoy, ns=(st.st_atime_ns, st.st_mtime_ns))

    proc = subprocess.run(
        rsync_argv('-aR', '--remove-source-files', str(root_src), f'{dest}/'),
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, cwd=base)
    ctx = f'rc={proc.returncode}, output={proc.stdout!r}'

    if not decoy.exists():
        test_fail(f'--remove-source-files deleted a same-named file in the '
                  f'sender CWD instead of the transferred source ({ctx})')
    if decoy.read_bytes() != DECOY_DATA:
        test_fail(f'the sender CWD file was modified ({ctx})')

    if proc.returncode != 0:
        test_fail(f'absolute -R --remove-source-files failed ({ctx})')
    if root_src.exists():
        test_fail(f'the requested source under / was not removed ({ctx})')

    copies = sorted(p for p in dest.rglob('*') if p.is_file())
    if len(copies) != 1 or copies[0].read_bytes() != SOURCE_DATA:
        test_fail(f'destination did not receive the real source: {copies} ({ctx})')
finally:
    if root_src.exists():
        root_src.unlink()

print('absolute --relative cleanup stayed anchored at / and spared the CWD')

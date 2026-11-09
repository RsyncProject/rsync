#!/usr/bin/env python3
"""An absolute --relative cleanup must not be re-anchored at the sender's CWD.

secure_sender_parent_fd() resolved the parent of an absolute --relative name
through the cwd-backed dirfd cache.  A source that is a direct child of / has
an *empty* parent component, so the cache handed back the sender's own working
directory and --remove-source-files unlinked a same-named entry there instead
of the file that was really transferred -- silently, with exit status 0.

The sibling test sender-remove-source-relative-anchor covers the nested case,
where the same defect merely fails the cleanup.  This one covers the case where
it destroys an unrelated file.

Requires root: reaching the empty-parent case needs a source directly under /.
"""

import os
import subprocess
from pathlib import Path

from rsyncfns import (
    SCRATCHDIR, makepath, rmtree, rsync_argv, test_fail, test_skipped,
)

if os.geteuid() != 0:
    test_skipped('needs root to place a transfer source directly under /')

SOURCE_DATA = b'REAL-SOURCE-CONTENT'
DECOY_DATA = b'CWD-DECOY-CONTENT!!'
assert len(SOURCE_DATA) == len(DECOY_DATA)   # same size: only the name matches

base = SCRATCHDIR / 'sender-remove-root-anchor'
rmtree(base)
dest = base / 'dst'
makepath(dest)

# Unique so a stray file in / can never be mistaken for ours.
root_src = Path('/') / f'rsync-root-anchor-probe-{os.getpid()}'
decoy = base / root_src.name

try:
    try:
        root_src.write_bytes(SOURCE_DATA)
    except OSError as e:
        # e.g. macOS's sealed system volume: / is read-only even for root.
        test_skipped(f'cannot create a transfer source under /: {e}')
    decoy.write_bytes(DECOY_DATA)
    # Same size and mtime, so the sender's "has it changed?" guard passes and
    # the decoy is a credible removal target.  Nanosecond precision matters:
    # the guard also compares sub-second mtime, so a whole-second copy would
    # make the sender skip the removal for an unrelated reason.
    st = root_src.stat()
    os.utime(decoy, ns=(st.st_atime_ns, st.st_mtime_ns))

    proc = subprocess.run(
        rsync_argv('-aR', '--remove-source-files', str(root_src), f'{dest}/'),
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, cwd=base)
    ctx = f'rc={proc.returncode}, output={proc.stdout!r}'

    # The security assertion first: whatever else happened, the unrelated
    # same-named file in the sender's CWD must be untouched.
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

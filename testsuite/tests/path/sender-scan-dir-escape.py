#!/usr/bin/env python3

import os
import subprocess
import time

from harness.rsync import (
    SCRATCHDIR, race_budget, rmtree, rsync_argv, rsync_supports,
    start_path_flipper, stop_flipper, test_fail,
)

MARKER = "XFIL-OUTSIDE-SYMLINK-TARGET-do-not-copy"
NKEEP = 64

base = SCRATCHDIR / 'scan-dir-escape'
src = base / 'src'
outside = base / 'outside'
dest = base / 'dest'

rmtree(base)

realsub = src / '.realsub'
realsub.mkdir(parents=True)
for i in range(NKEEP):
    (realsub / f'keep{i}').write_text("in-tree\n")

outside.mkdir(parents=True)
os.symlink(MARKER, outside / 'xfil_link')
(outside / 'xfil_dir').mkdir()
(outside / 'xfil_dir' / 'inner').write_text("x\n")
(outside / 'xfil_file').write_text("x\n")

os.rename(realsub, src / 'sub')
os.symlink('../outside', src / 'evil')
dest.mkdir(parents=True)

def escaped():
    d = dest / 'sub'
    if d.is_symlink() or not d.is_dir():
        return None
    link = d / 'xfil_link'
    if link.is_symlink() and os.readlink(link) == MARKER:
        return f"symlink target leaked: dest/sub/xfil_link -> {MARKER}"
    for name in ('xfil_dir', 'xfil_file', 'xfil_link'):
        if (d / name).exists() or (d / name).is_symlink():
            return f"out-of-tree name leaked: dest/sub/{name}"
    return None

extra = ['--no-inc-recursive'] if rsync_supports('--no-inc-recursive') else []

flip = start_path_flipper(src / 'sub', src / 'evil')
leak = None
deadline = time.monotonic() + race_budget()
try:
    while time.monotonic() < deadline and leak is None:
        rmtree(dest)
        dest.mkdir(parents=True)
        subprocess.run(rsync_argv('-a', *extra, f'{src}/', f'{dest}/'),
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        leak = escaped()
finally:
    stop_flipper(flip)

if leak is not None:
    test_fail(
        f'sender scan copied an out-of-tree entry through a flipped symlink: {leak}')
print("sender-scan-dir-escape: directory enumeration stayed within the transfer root")

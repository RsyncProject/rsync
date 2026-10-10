#!/usr/bin/env python3

import os
import platform
import subprocess
import time

from harness.mutation import race_budget, start_path_flipper, stop_flipper
from harness.rsync import SCRATCHDIR, rmtree, rsync_argv, rsync_supports, test_fail, test_xfail

_CYGWIN = platform.system().startswith('CYGWIN')

SECRET = "OUTSIDE-SECRET-do-not-copy"
NFILES = 64

base = SCRATCHDIR / 'srcrace'
src = base / 'src'
outside = base / 'outside'
dest = base / 'dest'

rmtree(base)
realsub = src / '.realsub'
realsub.mkdir(parents=True)
for i in range(NFILES):
    (realsub / f'loot{i}').write_text("in-tree dummy\n")
outside.mkdir(parents=True)
for i in range(NFILES):
    (outside / f'loot{i}').write_text(SECRET + "\n")
os.rename(realsub, src / 'sub')
os.symlink('../outside', src / 'evil')
dest.mkdir(parents=True)

def leaked():
    d = dest / 'sub'
    if d.is_symlink() or not d.is_dir():
        return False
    for f in d.iterdir():
        try:
            if f.read_text().strip() == SECRET:
                return True
        except OSError:
            pass
    return False

extra = ['--no-inc-recursive'] if rsync_supports('--no-inc-recursive') else []

flip = start_path_flipper(src / 'sub', src / 'evil')
won = False
deadline = time.monotonic() + race_budget()
try:
    while time.monotonic() < deadline:
        rmtree(dest)
        dest.mkdir(parents=True)
        subprocess.run(rsync_argv('-a', *extra, f'{src}/', f'{dest}/'),
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if leaked():
            won = True
            break
finally:
    stop_flipper(flip)

if won:
    if _CYGWIN:
        test_xfail(
            f"cygwin: source-tree parent-flip race still leaks ({SECRET!r}); the "
            "confined sender open is compiled in but Cygwin's symlink emulation "
            "does not enforce it -- documented platform residual.")
    test_fail(
        f"source-tree TOCTOU: content from outside the source tree ({SECRET!r}) "
        "was copied into the output -- the sender followed a parent-component "
        "symlink it should have confined.")

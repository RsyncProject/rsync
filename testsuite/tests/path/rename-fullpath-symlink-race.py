#!/usr/bin/env python3

import os
import subprocess
import time

from harness.mutation import race_budget
from harness.rsync import SCRATCHDIR, rmtree, rsync_argv, test_fail

base = SCRATCHDIR / f'rename-fullpath-{os.getpid()}'
src = base / 'src'
dest = base / 'dest'
outside = base / 'outside'
tmpd = base / 'tmpd'
rmtree(base)
(src / 'sub').mkdir(parents=True)
for i in range(40):
    (src / 'sub' / f'f{i}').write_text('payload\n')
dest.mkdir()
outside.mkdir()
tmpd.mkdir()

sub = dest / 'sub'
link = dest / '.sublink'
link.symlink_to(outside)

def push():
    subprocess.run(
        rsync_argv('-a', f'--temp-dir={tmpd}', f'{src}/', f'{dest}/'),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )

rmtree(sub)
sub.mkdir()
push()
if not (sub / 'f0').is_file():
    test_fail("positive control: a normal --temp-dir push did not write files "
              "into dest/sub, so the rename race window would be vacuous")
for f in outside.iterdir():
    f.unlink()

flip_code = (
    "import os, sys, time\n"
    "sub, link = sys.argv[1], sys.argv[2]\n"
    "scratch = sub + '.flip'\n"
    "parent = os.getppid()\n"
    "deadline = time.time() + 120\n"
    "while time.time() < deadline and os.getppid() == parent:\n"
    "    try:\n"
    "        os.makedirs(sub, exist_ok=True)\n"
    "        os.rename(sub, scratch); os.rename(link, sub); os.rename(scratch, link)\n"
    "        os.rename(sub, scratch); os.rename(link, sub); os.rename(scratch, link)\n"
    "    except OSError:\n"
    "        pass\n"
)
flip = subprocess.Popen(['python3', '-c', flip_code, str(sub), str(link)])
try:
    deadline = time.monotonic() + race_budget(10.0)
    while time.monotonic() < deadline:
        rmtree(sub)
        try:
            sub.unlink()
        except OSError:
            pass
        push()
        escaped = sorted(p.name for p in outside.iterdir()
                         if p.is_file() and not p.is_symlink())
        if escaped:
            test_fail(
                f'final rename escaped the destination through a flipped symlink: {escaped}')
finally:
    flip.terminate()
    try:
        flip.wait(timeout=5)
    except subprocess.TimeoutExpired:
        flip.kill()

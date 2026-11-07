#!/usr/bin/env python3

import os
import platform
import subprocess
import time

from harness.mutation import race_budget
from harness.rsync import (
    SCRATCHDIR, rmtree, rsync_argv, test_fail, test_skipped, xattr_set, xattrs_supported,
)

MARKER = 'user.marker'
N = 120

if platform.system() != 'Linux':
    test_skipped("parent-flip xattr race is checked on Linux (os.*xattr)",
                 capability='linux_xattr')
if not xattrs_supported():
    test_skipped("rsync built without xattr support (or no xattr tooling)", capability='xattr')

base = SCRATCHDIR / 'copy-xattrs-race'
src = base / 'src'
cdbasis = base / 'cdbasis'
dest = base / 'dest'
outside = base / 'outside'
DEEP = '/'.join(['d'] * 70)

rmtree(base)
(src / DEEP / 'sub').mkdir(parents=True)
(cdbasis / DEEP / 'sub').mkdir(parents=True)
(dest / DEEP).mkdir(parents=True)
outside.mkdir()

payload = ('x' * 4096 + '\n') * 16
try:
    for i in range(N):
        s = src / DEEP / 'sub' / f'f{i}'
        b = cdbasis / DEEP / 'sub' / f'f{i}'
        s.write_text(payload)
        b.write_text(payload)
        os.chmod(s, 0o644)
        os.chmod(b, 0o644)
        st = s.stat()
        os.utime(b, (st.st_atime, st.st_mtime))
        xattr_set('marker', 'PWNED', s, b)
        (outside / f'f{i}').write_text('')
except OSError as e:
        test_skipped(f"filesystem does not support user xattrs ({e})", capability='xattr')

sub = dest / DEEP / 'sub'
link = dest / DEEP / '.sublink'
link.symlink_to(outside)

def push():
    subprocess.run(
        rsync_argv('-rtpX', '--inplace', f'--copy-dest={cdbasis}',
                   f'{src}/', f'{dest}/'),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )

def outside_marked():
    return sorted(p.name for p in outside.iterdir()
                  if p.is_file() and not p.is_symlink()
                  and MARKER in os.listxattr(str(p)))

rmtree(sub)
sub.mkdir()
push()
if MARKER not in os.listxattr(str(sub / 'f0')):
    test_fail("positive control: a normal --copy-dest -X push did not copy the "
              "basis marker xattr into dest/sub/f0, so the copy_xattrs race "
              "window would be vacuous")
for i in range(N):
    try:
        os.removexattr(str(outside / f'f{i}'), MARKER)
    except OSError:
        pass

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
        marked = outside_marked()
        if marked:
            test_fail(
                "metadata xattr parent-symlink race: the attacker-chosen marker "
                f"xattr was written onto files OUTSIDE the destination tree "
                f"({marked}) -- a confined receiver's set_file_attrs() fell back to "
                "a path-based lsetxattr and followed a flipped dest/sub symlink.")
finally:
    flip.terminate()
    try:
        flip.wait(timeout=5)
    except subprocess.TimeoutExpired:
        flip.kill()

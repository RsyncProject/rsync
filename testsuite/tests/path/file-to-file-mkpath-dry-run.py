#!/usr/bin/env python3

import os
import subprocess

from harness.rsync import SCRATCHDIR, makepath, rmtree, rsync_argv, test_fail

def itemize(*args):
    p = subprocess.run(rsync_argv('-ai', *args), capture_output=True, text=True)
    return p.returncode, p.stdout + p.stderr

mk = SCRATCHDIR / 'mk'
rmtree(mk)
makepath(mk / 'from')
(mk / 'from' / 'src').write_text("payload\n")

drc, dry = itemize('--dry-run', '--mkpath',
                   str(mk / 'from' / 'src'), str(mk / 'dndir' / 'dst'))
rc, real = itemize('--mkpath', str(mk / 'from' / 'src'), str(mk / 'rdir' / 'dst'))
if drc != 0:
    print(dry)
    test_fail("--mkpath file-to-file --dry-run failed (#880)")
if not (mk / 'rdir' / 'dst').exists():
    test_fail("--mkpath real run did not create the file")
if dry.replace('dndir', 'X') != real.replace('rdir', 'X'):
    test_fail(f"--mkpath dry-run output differs from the real run:\n"
              f" dry : {dry!r}\n real: {real!r}")

ex = SCRATCHDIR / 'ex'
rmtree(ex)
makepath(ex / 'a')
makepath(ex / 'b')
(ex / 'src').write_text("brand new content\n")
for d in ('a', 'b'):
    (ex / d / 'dst').write_text("old\n")
    os.utime(ex / d / 'dst', (0, 0))

_, dry2 = itemize('--dry-run', str(ex / 'src'), str(ex / 'a' / 'dst'))
_, real2 = itemize(str(ex / 'src'), str(ex / 'b' / 'dst'))
if dry2 != real2:
    test_fail(f"file-to-file --dry-run misreports an existing destination:\n"
              f" dry : {dry2!r}\n real: {real2!r}")

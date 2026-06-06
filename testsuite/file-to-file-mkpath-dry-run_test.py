#!/usr/bin/env python3
# Regression test for issue #880 (and the dry-run itemize regression that the
# first proposed fix, PR #952, would have introduced).
#
# (1) Copying file-to-file with --mkpath and --dry-run used to abort with
#     "change_dir#3 ... failed", because make_path() only *reports* (does not
#     create) directories in a dry run, so the later chdir found no parent.
#
# (2) The fix must stay scoped to the missing-parent case: a plain
#     file-to-file --dry-run onto an *existing*, differing destination must
#     still itemize the real change, not report the file as brand new (PR #952
#     bumped dry_run unconditionally, which broke this).
#
# In both cases a "--dry-run -i" must produce the same itemized output as the
# real run.  Based on the test from PR #952 by Stiliyan Tonev.

import os
import subprocess

from rsyncfns import SCRATCHDIR, makepath, rmtree, rsync_argv, test_fail


def itemize(*args):
    p = subprocess.run(rsync_argv('-ai', *args), capture_output=True, text=True)
    return p.returncode, p.stdout + p.stderr


# (1) --mkpath file-to-file: the dry run must succeed and match the real run.
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

# (2) Plain file-to-file onto an existing, differing destination: the dry run
# must itemize the same change as the real run (a/dst and b/dst share the
# basename 'dst', so the itemized lines are directly comparable).
ex = SCRATCHDIR / 'ex'
rmtree(ex)
makepath(ex / 'a')
makepath(ex / 'b')
(ex / 'src').write_text("brand new content\n")
for d in ('a', 'b'):
    (ex / d / 'dst').write_text("old\n")
    os.utime(ex / d / 'dst', (0, 0))        # make size + mtime differ

_, dry2 = itemize('--dry-run', str(ex / 'src'), str(ex / 'a' / 'dst'))
_, real2 = itemize(str(ex / 'src'), str(ex / 'b' / 'dst'))
if dry2 != real2:
    test_fail(f"file-to-file --dry-run misreports an existing destination:\n"
              f" dry : {dry2!r}\n real: {real2!r}")

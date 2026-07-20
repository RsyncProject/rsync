#!/usr/bin/env python3
"""Regression test for issue #530: --compare-dest created every directory.

--compare-dest populates the destination with just the differences against the
compare hierarchy, but rsync created *every* source directory there, including
ones whose entire contents already matched.  A backup of 200 dirs where only 3
had changed produced 400 directories -- 197 of them completely empty -- which
is exactly the noise the reporter hit at scale (129k dirs for 23k real
changes).

rsync cannot know at mkdir time whether a descendant will differ, so a matching
directory is still created (which keeps its attribute handling intact and gives
any changed descendant a home) and is then removed again at the end if nothing
was actually placed in it.  Removing an entry bumps the containing directory's
mtime, so that is saved and restored.

We check the whole contract: unchanged subtrees leave nothing behind, changed
files still arrive (including several levels down), the directories that are
kept keep their permissions and mtimes, and a surviving parent keeps its mtime
even though a child directory was pruned out of it.
"""

import os

from rsyncfns import (
    FROMDIR, TODIR, TMPDIR, makepath, rmtree, run_rsync, test_fail,
)

compdir = TMPDIR / 'comp'

rmtree(FROMDIR)
rmtree(TODIR)
rmtree(compdir)
makepath(FROMDIR, TODIR, compdir)

# 40 dirs each holding a file; only 3 of them will differ.
NDIRS = 40
CHANGED = (7, 19, 33)
for i in range(1, NDIRS + 1):
    makepath(FROMDIR / f'd{i}' / 'sub')
    (FROMDIR / f'd{i}' / 'sub' / 'file').write_text(f'data {i}\n')

# A dir with distinctive perms/mtime that will be kept (it holds a change),
# plus a child dir that matches and must be pruned back out of it.
makepath(FROMDIR / 'keep' / 'gone')
(FROMDIR / 'keep' / 'changed').write_text('before\n')
(FROMDIR / 'keep' / 'gone' / 'f').write_text('static\n')

# An empty dir that matches -- it should not appear in the destination.
makepath(FROMDIR / 'emptydir')

# Seed the compare hierarchy with an exact copy, then introduce the changes.
run_rsync('-a', f'{FROMDIR}/', f'{compdir}/')
for i in CHANGED:
    (FROMDIR / f'd{i}' / 'sub' / 'file').write_text(f'CHANGED {i}\n')
(FROMDIR / 'keep' / 'changed').write_text('after\n')

os.chmod(FROMDIR / 'keep', 0o751)
KEEP_MTIME = 1556089689  # 2019-05-06 07:08:09 UTC
os.utime(FROMDIR / 'keep', (KEEP_MTIME, KEEP_MTIME))

run_rsync('-a', f'--compare-dest={compdir}', f'{FROMDIR}/', f'{TODIR}/')

# --- nothing unchanged may be left behind ----------------------------------
empty = [p for p in TODIR.rglob('*') if p.is_dir() and not any(p.iterdir())]
if empty:
    test_fail(f"--compare-dest left {len(empty)} empty directories behind, e.g. "
              f"{empty[0].relative_to(TODIR)} (issue #530)")

if (TODIR / 'emptydir').exists():
    test_fail("an unchanged empty dir was created in the destination")
if (TODIR / 'keep' / 'gone').exists():
    test_fail("an unchanged subdir was created in the destination")

for i in range(1, NDIRS + 1):
    if i not in CHANGED and (TODIR / f'd{i}').exists():
        test_fail(f"unchanged dir d{i} was created in the destination")

# --- everything that did change must still arrive, intact ------------------
for i in CHANGED:
    got = TODIR / f'd{i}' / 'sub' / 'file'
    if not got.is_file():
        test_fail(f"changed file under d{i} was not transferred")
    if got.read_text() != f'CHANGED {i}\n':
        test_fail(f"changed file under d{i} has the wrong contents")

if (TODIR / 'keep' / 'changed').read_text() != 'after\n':
    test_fail("changed file in keep/ has the wrong contents")

# --- a directory we keep must keep its metadata ----------------------------
st = os.stat(TODIR / 'keep')
if st.st_mode & 0o777 != 0o751:
    test_fail(f"kept dir lost its permissions: got {st.st_mode & 0o777:o}, want 751")
# Pruning keep/gone must not have disturbed keep/'s own mtime.
if int(st.st_mtime) != KEEP_MTIME:
    test_fail(f"pruning a child dir changed the parent's mtime: "
              f"got {int(st.st_mtime)}, want {KEEP_MTIME}")

print("issue #530: --compare-dest left no empty dirs, kept the changes, "
      "and preserved directory metadata")

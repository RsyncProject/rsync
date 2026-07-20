#!/usr/bin/env python3
"""Regression test for clean_fname()'s ".." collapsing.

clean_fname(name, CFN_COLLAPSE_DOT_DOT_DIRS) is rsync's canonical path
normalizer.  Commit 9e089846 ("util: fixed issue in clean_fname()") fixed a
read-before-buffer in the ".." back-up loop, but the new loop leaves 's'
pointing at the *start* of the preceding component (just past its '/'),
whereas the old loop left it *at* the '/'.  The surrounding test and
assignment (`*s == '/'` / `t = s + 1`) were not adjusted for that one-byte
shift, so the collapse silently stopped working for every path except a
single leading no-slash component:

    a/../b              -> b          (still worked)
    /a/../b             -> /a/../b    (BROKEN: should be /b)
    a/b/../c            -> a/b/../c   (BROKEN: should be a/c)
    x/y/../../z         -> x/y/../../z(BROKEN: should be z)

We exercise it through a merge-filter filename, one of the callers that pass
CFN_COLLAPSE_DOT_DOT_DIRS.  The filter file lives at "a/c"; we reference it as
"a/none/../c" where the "none" directory does NOT exist.  A correct collapse
turns that into "a/c" (which opens), so the filter is read and applied; the
buggy code leaves the literal "a/none/../c", which fails to open (ENOENT) and
aborts the whole run.  This is also a faithful reproduction of the user-facing
regression: merge/exclude files referenced via a path containing "..".
"""

import os

from rsyncfns import (
    FROMDIR, TODIR, rmtree, run_rsync, test_fail,
)

rmtree(FROMDIR)
rmtree(TODIR)
FROMDIR.mkdir(parents=True, exist_ok=True)
TODIR.mkdir(parents=True, exist_ok=True)

# Source: one file to keep, one the filter should drop.
(FROMDIR / 'keep.txt').write_text('keep\n')
(FROMDIR / 'drop.skip').write_text('drop\n')

# Merge-filter file at a/c; note that a/none does NOT exist, so the path
# "a/none/../c" only resolves if rsync collapses ".." to a plain "a/c".
adir = TODIR.parent / 'a'
rmtree(adir)
adir.mkdir(parents=True, exist_ok=True)
(adir / 'c').write_text('- *.skip\n')

# Interior collapse only exercises the case where the ".." eats a component
# that has a '/' before it.  Two more filter files pin the other two branches:
# 'f1' for a leading component (the s == name case, where there is no earlier
# '/' to find) and 'updir' for a leading ".." that must NOT be collapsed away.
(TODIR.parent / 'f1').write_text('- *.skip\n')
(TODIR.parent / 'updir').write_text('- *.skip\n')
deepdir = TODIR.parent / 'sub1' / 'sub2'
rmtree(deepdir)
deepdir.mkdir(parents=True, exist_ok=True)


def check(rundir, filter_path, what):
    """Run with the merge filter named via `filter_path` and require that the
    file was found *and* applied.  Each name only resolves when clean_fname()
    collapses (or preserves) the ".." exactly right; get it wrong and the open
    fails or lands on a different file, so nothing filters *.skip out."""
    rmtree(TODIR)
    TODIR.mkdir(parents=True, exist_ok=True)
    saved = os.getcwd()
    os.chdir(rundir)
    try:
        proc = run_rsync('-a', f'--filter=merge {filter_path}',
                         f'{FROMDIR}/', f'{TODIR}/', check=False)
    finally:
        os.chdir(saved)

    if proc.returncode != 0:
        test_fail(f"rsync could not open the merge filter via '{filter_path}' "
                  f"({what}); clean_fname() mishandled the '..'")
    if not (TODIR / 'keep.txt').is_file():
        test_fail(f"keep.txt was not transferred ({what})")
    if (TODIR / 'drop.skip').exists():
        test_fail(f"drop.skip was transferred ({what}): the merge filter was "
                  "not applied, so clean_fname() resolved the filter path to "
                  "the wrong file")


# Interior component: "a/none/../c" -> "a/c" (a/none does not exist).
check(TODIR.parent, 'a/none/../c', 'interior component')

# Leading component: "zzz/../f1" -> "f1".  Here the back-up reaches the start
# of the name with no '/' before it, so a bounds slip or an off-by-one in that
# test stops the collapse (this is the shape the 9e089846 regression left
# working, and the shape a partial revert breaks).
check(TODIR.parent, 'zzz/../f1', 'leading component')

# A leading ".." has nothing to collapse into and must be kept: run two levels
# down and reach back up.  If it were collapsed away the name would resolve
# inside sub1/sub2, where no such file exists.
check(deepdir, '../../updir', 'leading ".." preserved')

print("OK: clean_fname() collapsed interior and leading components and kept a "
      "leading '..'; the merge filter applied in every case")

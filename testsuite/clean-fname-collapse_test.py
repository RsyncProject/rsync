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

saved = os.getcwd()
os.chdir(TODIR.parent)
try:
    proc = run_rsync('-a', '--filter=merge a/none/../c',
                     f'{FROMDIR}/', f'{TODIR}/', check=False)
finally:
    os.chdir(saved)

if proc.returncode != 0:
    test_fail(
        "rsync failed to open the merge filter via 'a/none/../c'; "
        "clean_fname() did not collapse '..' (regression of 9e089846)")

if not (TODIR / 'keep.txt').is_file():
    test_fail("keep.txt was not transferred")
if (TODIR / 'drop.skip').exists():
    test_fail("drop.skip was transferred -- the merge filter was not applied, "
              "so clean_fname() resolved the filter path to the wrong file")

print("OK: clean_fname() collapsed 'a/none/../c' -> 'a/c' and the filter applied")

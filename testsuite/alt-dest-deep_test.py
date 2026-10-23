#!/usr/bin/env python3
"""Property-level coverage of --link-dest / --copy-dest / --compare-dest at
depth and across directory boundaries.

The existing alt-dest_test.py is a faithful port that checks tree equality;
this companion asserts the *distinguishing* property of each option, at every
level of a >=3-deep tree, with the alternate-destination tree placed OUTSIDE
both the source and destination trees (a sibling, not a parent/child):

  --link-dest    unchanged files are HARD-LINKED to the reference (same inode);
                 a changed file is transferred fresh (not linked).
  --copy-dest    unchanged files are COPIED from the reference (never linked);
                 dest is complete and byte-identical to the source.
  --compare-dest unchanged files are NEITHER transferred NOR created in dest;
                 only a changed/new file lands in dest.

These options drive the two-dirfd / outside-tree path handling the resolver
restructure rewrites, so they must be guarded at depth.
"""

import os

from rsyncfns import (
    FROMDIR, SCRATCHDIR, TODIR,
    assert_exists, assert_hardlinked, assert_not_exists, assert_not_hardlinked,
    assert_same, make_tree, rmtree, run_rsync, walk_files,
)

src = FROMDIR
ref = SCRATCHDIR / 'altref'   # sibling of from/ and to/ -- outside both trees

rmtree(src)
rmtree(ref)
rmtree(TODIR)

# A >=3-deep source: f0 at the root, then d1/f1, d1/d2/f2, d1/d2/d3/f3.
make_tree(src, depth=3, data=True)

# Reference tree == an exact copy of the source, so every file is "identical"
# for the alt-dest comparison.
run_rsync('-a', f'{src}/', f'{ref}/')

# Now make the deepest file differ so it must be transferred in every mode.
changed = os.path.join('d1', 'd2', 'd3', 'f3')
with open(src / changed, 'ab') as f:
    f.write(b'a changed deep tail\n')

rels = [p.relative_to(src) for p in walk_files(src)]
assert os.path.join('d1', 'd2', 'd3', 'f3') in [str(r) for r in rels]


def run_to(opt):
    rmtree(TODIR)
    run_rsync('-a', f'--{opt}={ref}', f'{src}/', f'{TODIR}/')


# --- --link-dest: unchanged -> hardlink to ref; changed -> fresh copy -------
run_to('link-dest')
for rel in rels:
    d, r = TODIR / rel, ref / rel
    if str(rel) == changed:
        assert_not_hardlinked(d, r, label=f'link-dest changed {rel}')
        assert_same(d, src / rel, label=f'link-dest changed {rel}')
    else:
        assert_hardlinked(d, r, label=f'link-dest unchanged {rel}')

# --- --copy-dest: every file copied (never linked), dest complete -----------
run_to('copy-dest')
for rel in rels:
    d, r = TODIR / rel, ref / rel
    assert_exists(d, label=f'copy-dest {rel}')
    assert_same(d, src / rel, label=f'copy-dest {rel}')
    assert_not_hardlinked(d, r, label=f'copy-dest {rel}')

# --- --compare-dest: unchanged absent from dest; only the changed file lands -
run_to('compare-dest')
for rel in rels:
    d = TODIR / rel
    if str(rel) == changed:
        assert_exists(d, label=f'compare-dest changed {rel}')
        assert_same(d, src / rel, label=f'compare-dest changed {rel}')
    else:
        assert_not_exists(d, label=f'compare-dest unchanged {rel}')

print("alt-dest-deep: link-dest/copy-dest/compare-dest verified at depth")

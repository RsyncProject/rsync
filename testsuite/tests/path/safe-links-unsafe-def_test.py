#!/usr/bin/env python3
"""Pin what `--safe-links` / `--copy-unsafe-links` call an "unsafe" symlink, and
the "top of the transfer" cut-off.

The rsync(1) docs define an unsafe symlink only loosely: --safe-links "ignore[s]
symlinks that point outside the destination tree", and --copy-unsafe-links acts
on links "that point outside the copied tree", with the cut-off described as
"the top of the transfer ... the part of the path that rsync isn't mentioning in
the verbose output".  That last phrase is not something a user can test against.

The actual rule (unsafe_symlink() in util1.c) is purely LEXICAL on the link
value relative to the link's depth within the transfer:
  - any absolute target ('/...') is unsafe;
  - a relative target is unsafe iff its ".." count rises above the transfer top.
It does NOT resolve the target on disk.  This test pins both the per-shape
verdict and the trailing-slash cut-off, measured (not assumed), so the doc can
state the rule concretely.  Pure local client behaviour: no daemon/root/tcp.

Cross-version: the lexical rule is long-standing; expected identical against
--rsync-bin=old_versions/rsync_3.2.7.
"""

import os
import subprocess

from rsyncfns import SCRATCHDIR, rmtree, rsync_argv, test_fail

base = SCRATCHDIR / 'safe-links'
rmtree(base)
base.mkdir(parents=True)

# A target that physically exists ABOVE every transfer root, to prove the rule
# is lexical (verdict doesn't depend on whether the target resolves).
(base / 'sibling').write_text('SIBLING\n')

# tree/ is the thing we transfer.  Build the symlink shapes inside it.
tree = base / 'tree'
tree.mkdir()
(tree / 'file').write_text('FILE\n')
(tree / 'sub').mkdir()
(tree / 'sub' / 'subfile').write_text('SUBFILE\n')

SHAPES = {
    'rel-same-dir':   ('link_a', 'file'),               # -> tree/file     SAFE
    'rel-into-sub':   ('link_b', 'sub/subfile'),        # -> tree/sub/...  SAFE
    'rel-up-inside':  ('sub/link_c', '../file'),        # -> tree/file     SAFE
    'rel-escape':     ('link_d', '../sibling'),         # -> base/sibling  UNSAFE
    'rel-deep-escape':('sub/link_e', '../../sibling'),  # -> base/sibling  UNSAFE
    'abs-target':     ('link_f', str(base / 'sibling')),# absolute         UNSAFE
}
for _name, (where, val) in SHAPES.items():
    lp = tree / where
    lp.parent.mkdir(parents=True, exist_ok=True)
    os.symlink(val, lp)

# Expected unsafe verdict when the transfer top is `tree` (the usual case).
UNSAFE = {'rel-escape', 'rel-deep-escape', 'abs-target'}


def run(*args):
    subprocess.run(rsync_argv('-a', *args), stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL)


def fresh(dst):
    rmtree(dst)
    dst.mkdir(parents=True)


grid, mism = [], []

# --- --safe-links: unsafe links are DROPPED, safe links KEPT (top = tree) ----
dst = base / 'safe_dst'
fresh(dst)
run('--safe-links', f'{tree}/', f'{dst}/')
for name, (where, _val) in SHAPES.items():
    present = (dst / where).is_symlink()
    want_present = name not in UNSAFE
    grid.append(f"safe-links {name:15} present={int(present)} want={int(want_present)}")
    if present != want_present:
        mism.append(f"--safe-links {name}: present={present}, expected={want_present}")

# --- --copy-unsafe-links: unsafe links DEREFERENCED to a real file, safe kept -
dst = base / 'copy_dst'
fresh(dst)
run('--copy-unsafe-links', f'{tree}/', f'{dst}/')
for name, (where, _val) in SHAPES.items():
    p = dst / where
    is_link = p.is_symlink()
    is_file = p.is_file() and not is_link
    if name in UNSAFE:
        # dereferenced: a real file/dir, not a symlink
        ok = is_file
        grid.append(f"copy-unsafe {name:15} deref={int(is_file)} want=1")
    else:
        ok = is_link
        grid.append(f"copy-unsafe {name:15} keptlink={int(is_link)} want=1")
    if not ok:
        mism.append(f"--copy-unsafe-links {name}: is_link={is_link} is_file={is_file}")

# --- cut-off: the SAME link is safe or unsafe depending on the transfer top ---
# `tree/sub/link_c` has value '../file'.  When `sub` is a name INSIDE the
# transfer (top = tree) the link resolves within the top -> SAFE/kept.  When we
# transfer `sub/` itself (top = sub) the same '../file' rises above the top ->
# UNSAFE/dropped.  This is the "top of the transfer" cut-off, made concrete.
dst = base / 'cut_inside'
fresh(dst)
run('--safe-links', f'{tree}/', f'{dst}/')        # top = tree
kept_inside = (dst / 'sub' / 'link_c').is_symlink()
grid.append(f"cutoff top=tree   sub/link_c kept={int(kept_inside)} want=1")
if not kept_inside:
    mism.append("cut-off: sub/link_c (../file) should be SAFE when top=tree")

dst = base / 'cut_sub'
fresh(dst)
run('--safe-links', f'{tree}/sub/', f'{dst}/')    # top = sub
kept_sub = (dst / 'link_c').is_symlink()
grid.append(f"cutoff top=sub    link_c     kept={int(kept_sub)} want=0")
if kept_sub:
    mism.append("cut-off: link_c (../file) should be UNSAFE when top=sub")

if os.environ.get('REPORT_SAFE'):
    test_fail("REPORT-SAFE grid:\n  " + "\n  ".join(grid))
if mism:
    test_fail("safe-links/copy-unsafe-links contract deviated:\n  " + "\n  ".join(mism))

print("safe-links-unsafe-def: unsafe == absolute target OR a relative target "
      "whose '..' depth rises above the transfer top (lexical, not resolved); "
      "--safe-links drops unsafe links, --copy-unsafe-links dereferences them; "
      "the same '../file' link flips safe->unsafe as the transfer top moves down")

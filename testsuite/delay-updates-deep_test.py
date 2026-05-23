#!/usr/bin/env python3
"""Property-level coverage of --delay-updates at depth.

--delay-updates writes each updated file into a per-directory staging dir
(.~tmp~) during the transfer and only renames them into place at the very end,
so an interrupted run never leaves a half-written file visible. The staging dir
sits inside each destination directory, so the staging write and the
end-of-run rename are parent-directory operations the resolver restructure
rewrites; the ported delay-updates_test.py only exercises the tree root, so
this companion drives a >=3-deep tree.

Asserts, at every level of the tree:
  * a --delay-updates transfer reproduces the source and leaves no .~tmp~
    staging directory behind;
  * a stale file pre-planted in a deep .~tmp~ staging dir is overwritten
    cleanly and the staging dir is removed.
"""

import os

from rsyncfns import (
    FROMDIR, TODIR,
    assert_same, make_tree, rmtree, run_rsync, test_fail, walk_dirs,
    walk_files,
)

src = FROMDIR
deepdir = os.path.join('d1', 'd2', 'd3')


def no_staging_left():
    leftover = [p for p in walk_dirs(TODIR) if p.name == '.~tmp~']
    if leftover:
        test_fail(f"--delay-updates left staging dirs behind: {leftover}")


# --- initial --delay-updates over a deep tree -------------------------------
rmtree(src)
rmtree(TODIR)
make_tree(src, depth=3, data=True, data_size=4096)
rels = [p.relative_to(src) for p in walk_files(src)]

run_rsync('-a', '--delay-updates', f'{src}/', f'{TODIR}/')
for rel in rels:
    assert_same(TODIR / rel, src / rel, label=f'delay-updates initial {rel}')
no_staging_left()

# --- update every file, with a stale staging file planted deep --------------
for rel in rels:
    with open(src / rel, 'ab') as f:
        f.write(b'\nupdated content\n')

stage = TODIR / deepdir / '.~tmp~'
stage.mkdir(parents=True, exist_ok=True)
(stage / 'f3').write_bytes(b'stale staged junk\n')   # must be overwritten

run_rsync('-a', '--delay-updates', f'{src}/', f'{TODIR}/')
for rel in rels:
    assert_same(TODIR / rel, src / rel, label=f'delay-updates update {rel}')
no_staging_left()

print("delay-updates-deep: staging + clean overwrite verified at depth")

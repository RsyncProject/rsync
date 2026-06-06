#!/usr/bin/env python3
"""Coverage of -O (--omit-dir-times) and -J (--omit-link-times) at depth.

-O preserves file mtimes but leaves directory mtimes alone; -J does the same
for symlinks. Verify the distinction deep in the tree: the preserved entries
match the source, the omitted ones do not.
"""

import os

from rsyncfns import (
    FROMDIR, TODIR,
    assert_mtime_close, make_tree, rmtree, run_rsync, test_fail,
    walk_dirs, walk_files,
)

src = FROMDIR
OLD = 1_400_000_000


def seed():
    rmtree(src)
    rmtree(TODIR)
    make_tree(src, depth=3)
    for p in list(walk_files(src)) + list(walk_dirs(src)):
        os.utime(p, (OLD, OLD))


# --- -O: file mtimes preserved, directory mtimes omitted --------------------
seed()
run_rsync('-rlt', '-O', f'{src}/', f'{TODIR}/')
for f in walk_files(src):
    assert_mtime_close(TODIR / f.relative_to(src), OLD, label=f'-O file {f.name}')
# Every directory mtime must be omitted (left at ~now), not just one of them:
# the old "at least one differs" check would miss a bug that preserved some.
for d in walk_dirs(src):
    rel = d.relative_to(src)
    if abs(os.stat(TODIR / rel).st_mtime - OLD) <= 1:
        test_fail(f"-O preserved the mtime of directory {rel} instead of "
                  "omitting it")

# --- -J: symlink mtime omitted (where the platform records symlink mtimes) --
seed()
deep = os.path.join('d1', 'd2', 'd3')
os.symlink('f3', src / deep / 'sl')
try:
    os.utime(src / deep / 'sl', (OLD, OLD), follow_symlinks=False)
except (NotImplementedError, OSError):
    print("omit-times: -J check skipped (no symlink-mtime support here)")
else:
    run_rsync('-rlt', '-J', f'{src}/', f'{TODIR}/')
    dst = TODIR / deep / 'sl'
    if not os.path.islink(dst):
        test_fail("-J test: symlink was not copied")
    if abs(os.lstat(dst).st_mtime - OLD) <= 1:
        test_fail("-J did not omit the symlink mtime")

print("omit-times: -O omits dir times, -J omits link times (at depth)")

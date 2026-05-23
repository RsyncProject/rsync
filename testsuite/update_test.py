#!/usr/bin/env python3
"""Coverage of -u (--update) and --force at depth.

-u skips any destination file that is newer than the source. --force lets rsync
delete a non-empty destination directory when it must be replaced by a
non-directory. Both decide a per-entry action on a name whose parent chain the
resolver restructure rewrites, so check them several levels deep.
"""

import os

from rsyncfns import (
    FROMDIR, TODIR,
    assert_same, make_tree, makepath, rmtree, run_rsync, test_fail,
)

src = FROMDIR
deep = os.path.join('d1', 'd2', 'd3', 'f3')


# --- -u keeps a newer destination file, updates an older one ----------------
rmtree(src)
rmtree(TODIR)
make_tree(src, depth=3)
run_rsync('-a', f'{src}/', f'{TODIR}/')

# Make the deep source newer in content, but the DEST copy newer in time.
(src / deep).write_text("new source content\n")
keep = "destination is newer - keep me\n"
(TODIR / deep).write_text(keep)
st = os.stat(src / deep)
os.utime(TODIR / deep, (st.st_atime, st.st_mtime + 100))   # dest mtime newer

run_rsync('-a', '-u', f'{src}/', f'{TODIR}/')
if (TODIR / deep).read_text() != keep:
    test_fail("-u overwrote a destination file that was newer than the source")

# An older destination file IS updated under -u.
os.utime(TODIR / deep, (st.st_atime, st.st_mtime - 100))    # dest mtime older
run_rsync('-a', '-u', f'{src}/', f'{TODIR}/')
assert_same(TODIR / deep, src / deep, label='-u updated an older dest file')

# --- --force replaces a non-empty dest directory with a file at depth -------
rmtree(src)
rmtree(TODIR)
makepath(src / 'd1' / 'd2' / 'd3')
(src / deep).write_text("now a regular file\n")        # src: d1/d2/d3/f3 = file
makepath(TODIR / 'd1' / 'd2' / 'd3' / 'f3')             # dest: f3 = non-empty dir
(TODIR / 'd1' / 'd2' / 'd3' / 'f3' / 'occupant').write_text("blocker\n")

# Without --force the non-empty directory can't be replaced.
proc = run_rsync('-a', f'{src}/', f'{TODIR}/', check=False)
if proc.returncode == 0 and (TODIR / deep).is_file():
    test_fail("a non-empty directory was replaced by a file without --force")

# With --force the directory is removed and the file takes its place.
run_rsync('-a', '--force', f'{src}/', f'{TODIR}/')
if not (TODIR / deep).is_file():
    test_fail("--force did not replace the directory with the file at depth")
assert_same(TODIR / deep, src / deep, label='--force replacement content')

print("update: -u keeps newer dest / updates older; --force replaces a dir at depth")

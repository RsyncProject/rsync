#!/usr/bin/env python3
# Regression: a non-daemon receiver must FOLLOW an absolute symlinked
# destination directory -- the common `/backup -> /mnt/disk`, `/var/www ->
# /srv/www` admin pattern that scripts/cron use with absolute paths.
#
# The dest-symlink hardening opened the operator-named absolute dest with
# O_NOFOLLOW and *refused* a symlink ("refusing to chdir into symlinked
# destination ... use --insecure-links"), exiting 3.  A relative symlinked dest
# always followed (plain chdir), so the behaviour was inconsistent too.  Now a
# real directory is still opened O_NOFOLLOW (never reached via a raced leaf
# symlink), but an operator-named symlinked dest is followed; the per-entry
# transfer stays confined beneath the resolved root.

import os

from rsyncfns import SCRATCHDIR, assert_same, rmtree, run_rsync, test_fail

base = SCRATCHDIR / 'dest-symlinked'
src = base / 'src'
real = base / 'realdest'
link = base / 'backup'           # absolute symlink -> realdest
rmtree(base)
src.mkdir(parents=True)
real.mkdir(parents=True)
(src / 'f.txt').write_text('content\n')
(src / 'sub').mkdir()
(src / 'sub' / 'g.txt').write_text('deep\n')
os.symlink(str(real), str(link))     # ABSOLUTE symlinked destination

# Absolute dest path through the symlink -- this exited 3 on the unfixed branch.
run_rsync('-a', f'{src}/', f'{link}/')   # check=True: must succeed (exit 0)

for rel in ('f.txt', 'sub/g.txt'):
    got = real / rel
    if not got.is_file():
        test_fail("transfer through an absolute symlinked dest did not land in "
                  f"the real directory: {got} missing")
    assert_same(src / rel, got, label=rel)

print("dest-symlinked-dir: a non-daemon receiver follows an absolute symlinked dest")

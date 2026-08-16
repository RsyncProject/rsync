#!/usr/bin/env python3
import shutil

from rsyncfns import run_checked, setup_chroot_inner
from rsyncfns import makepath, rsync_argv, test_fail

base, inner, outside, src, url = setup_chroot_inner('chroot-comparedest-inner')
makepath(inner / 'dest')
(src / 'f').write_text('same\n')
shutil.copy2(src / 'f', outside / 'f')
proc, out = run_checked(rsync_argv('-a', '--compare-dest=../linkparent', f'{src}/', f'{url}mod/dest/'))
if not (inner / 'dest' / 'f').exists():
    test_fail(f"compare-dest used outside-inner-module metadata to suppress the transfer:\n{out}")
print("chroot-alt-dest-inner-module: outside compare-dest did not suppress destination creation")

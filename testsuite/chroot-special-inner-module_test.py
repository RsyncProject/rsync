#!/usr/bin/env python3
import os

from rsyncfns import run_checked, setup_chroot_inner
from rsyncfns import rsync_argv, test_fail

base, inner, outside, src, url = setup_chroot_inner('chroot-special-inner')
os.symlink('target', src / 'link')
proc, out = run_checked(rsync_argv('-a', str(src / 'link'), f'{url}mod/linkparent/link'))
if (outside / 'link').exists() or os.path.islink(outside / 'link'):
    test_fail(f"symlink creation escaped inner module through symlinked parent:\n{out}")
print("chroot-special-inner-module: symlink creation did not escape inner module")

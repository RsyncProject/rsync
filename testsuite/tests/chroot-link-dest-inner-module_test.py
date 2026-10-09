#!/usr/bin/env python3
import os
import shutil

from rsyncfns import run_checked, setup_chroot_inner
from rsyncfns import makepath, rsync_argv, test_fail

base, inner, outside, src, url = setup_chroot_inner('chroot-linkdest-inner')
makepath(inner / 'dest')
(src / 'f').write_text('same\n')
shutil.copy2(src / 'f', outside / 'f')
proc, out = run_checked(rsync_argv('-a', '--link-dest=../linkparent', f'{src}/', f'{url}mod/dest/'))
dest = inner / 'dest' / 'f'
if not dest.exists():
    test_fail(f"link-dest transfer did not create destination file:\n{out}")
if (dest.stat().st_dev, dest.stat().st_ino) == ((outside / 'f').stat().st_dev, (outside / 'f').stat().st_ino):
    test_fail(f"link-dest hardlinked an outside-inner-module file:\n{out}")
print("chroot-link-dest-inner-module: outside basis was not hardlinked into inner module")

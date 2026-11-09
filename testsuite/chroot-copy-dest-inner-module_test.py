#!/usr/bin/env python3
# A /./ inner-module chroot daemon must not use an out-of-inner-module file as a
# --copy-dest basis (here reached via inner/linkparent -> ../outside).
#
# Detection is CONTENT-based, not atime-based: poison outside/f with distinct
# content but an identical size+mtime, so the --copy-dest size+mtime quick-check
# would still accept it if confinement were bypassed -- and copy_file() would then
# copy the poison bytes into dest.  If the confinement holds, the basis is refused
# and dest is transferred from src.  (The previous atime oracle false-positived on
# macOS: Spotlight, mds/mdworker_shared, indexes the freshly-created scratch files
# and reads outside/f, advancing its atime independently of rsync.  Any external
# reader -- indexer, AV, backup -- defeats an atime signal; content does not.)

import os
import shutil

from rsyncfns import run_checked, setup_chroot_inner
from rsyncfns import makepath, rsync_argv, test_fail

base, inner, outside, src, url = setup_chroot_inner('chroot-copydest-inner')
makepath(inner / 'dest')
(src / 'f').write_text('same\n')                 # 5 bytes
shutil.copy2(src / 'f', outside / 'f')           # same size + mtime as src/f
(outside / 'f').write_text('PWND\n')             # distinct content, same 5 bytes
src_mtime = (src / 'f').stat().st_mtime
os.utime(outside / 'f', (src_mtime, src_mtime))  # restore mtime for the quick-check

proc, out = run_checked(rsync_argv('-a', '--copy-dest=../linkparent', f'{src}/', f'{url}mod/dest/'))
dest = inner / 'dest' / 'f'
if not dest.exists():
    test_fail(f"copy-dest transfer did not create destination file:\n{out}")
# A real escape copies the out-of-module basis content into dest; a confined
# daemon transfers from src instead.
if dest.read_text() != (src / 'f').read_text():
    test_fail("copy-dest read an outside-inner-module basis file: dest got the "
              f"out-of-module content {dest.read_text()!r}:\n{out}")
print("chroot-copy-dest-inner-module: outside copy-dest basis was not read")

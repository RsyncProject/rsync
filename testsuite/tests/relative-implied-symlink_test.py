"""Pin that `-R`/`--relative` materializes implied parent dirs as REAL
directories even when a sending-side path element is a symlink.

The rsync(1) `--relative` entry claims: "Beginning with rsync 3.0.0, rsync
always sends these implied directories as real directories in the file list,
even if a path element is really a symlink on the sending side.  This prevents
some really unexpected behaviors..."  This test checks the "always" by making an
implied path element a symlink on the sender and confirming the receiver creates
it as a real directory (not a symlink), so the deep file lands under a real dir.

Pure local client behaviour: no daemon/root/tcp.  Cross-version: expected
identical against --rsync-bin=old_versions/rsync_3.2.7 (3.2.7 >= 3.0.0).
"""

import os
import subprocess

from rsyncfns import SCRATCHDIR, rmtree, rsync_argv, test_fail

base = SCRATCHDIR / 'relative-implied'
rmtree(base)
base.mkdir(parents=True)

src = base / 'src'
(src / 'realdir').mkdir(parents=True)
(src / 'realdir' / 'file').write_text('F\n')
os.symlink('realdir', src / 'link')        # 'link' is a symlink path element

dst = base / 'dst'
rmtree(dst)
dst.mkdir()
# Transfer through the symlink path element with -R: src/./link/file
subprocess.run(rsync_argv('-a', '-R', f'{src}/./link/file', f'{dst}/'),
               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

p = dst / 'link'
if p.is_symlink():
    test_fail("--relative: implied 'link' was sent as a symlink, not materialized "
              "as a real directory (the 'always sends ... as real directories' "
              "claim would be false)")
if not p.is_dir():
    test_fail("--relative: implied 'link' was not created as a directory")
f = dst / 'link' / 'file'
if not f.is_file() or f.read_text() != 'F\n':
    test_fail("--relative: deep file did not land under the materialized dir")

print("relative-implied-symlink: -R materializes an implied path element as a "
      "REAL directory on the receiver even when it is a symlink on the sender -- "
      "the 'always sends implied dirs as real directories' claim holds")

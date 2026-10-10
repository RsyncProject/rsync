#!/usr/bin/env python3

import subprocess

from harness.rsync import SCRATCHDIR, rmtree, rsync_argv, test_fail

base = SCRATCHDIR / 'backup-dir-rel'
rmtree(base)
base.mkdir(parents=True)

src = base / 'src'
dst = base / 'dst'
src.mkdir()
dst.mkdir()
(dst / 'file').write_text('OLD\n')
(src / 'file').write_text('NEWER-CONTENT\n')

subprocess.run(
    rsync_argv('-a', '-b', '--backup-dir=bak', f'{src}/', f'{dst}/'),
    cwd=str(base), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

dest_rel = dst / 'bak' / 'file'
cwd_rel = base / 'bak' / 'file'

if cwd_rel.exists():
    test_fail("--backup-dir: relative path was interpreted relative to the cwd, "
              "not the destination dir")
if not dest_rel.is_file() or dest_rel.read_text() != 'OLD\n':
    test_fail("--backup-dir: backup did not land under the destination dir "
              "(expected dst/bak/file with the old content)")
if (dst / 'file').read_text() != 'NEWER-CONTENT\n':
    test_fail("--backup-dir: destination file was not updated")

print("backup-dir-relative: a relative --backup-dir lands under the destination "
      "dir (dst/bak/file), not the invoking cwd")

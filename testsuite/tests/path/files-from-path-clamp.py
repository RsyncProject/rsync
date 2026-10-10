#!/usr/bin/env python3

import subprocess

from harness.rsync import SCRATCHDIR, rmtree, rsync_argv, test_fail

base = SCRATCHDIR / 'files-from-clamp'
rmtree(base)
base.mkdir(parents=True)

src = base / 'src'
src.mkdir()
(src / 'file').write_text('INSRC-FILE\n')
(src / 'keep').write_text('INSRC-KEEP\n')
(base / 'keep').write_text('OUTSIDE-KEEP-MUST-NOT-APPEAR\n')

listf = base / 'list'
listf.write_text('/file\nkeep\n../keep\n')

dst = base / 'dst'
rmtree(dst)
dst.mkdir()
proc = subprocess.run(
    rsync_argv('-a', f'--files-from={listf}', f'{src}/', f'{dst}/'),
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

f = dst / 'file'
if not f.is_file() or f.read_text() != 'INSRC-FILE\n':
    test_fail("--files-from: leading-slash '/file' was not transferred relative "
              "to the source dir")

k = dst / 'keep'
if not k.is_file():
    test_fail("--files-from: 'keep' did not transfer")
if k.read_text() != 'INSRC-KEEP\n':
    test_fail(f"--files-from: '../keep' escaped the source dir -- dst/keep is "
              f"{k.read_text()!r}, expected the in-source 'INSRC-KEEP'")

if (base / 'keep').read_text() != 'OUTSIDE-KEEP-MUST-NOT-APPEAR\n':
    test_fail("--files-from: the outside 'keep' was modified")

print("files-from-path-clamp: leading '/' stripped (entry taken relative to the "
      "source); '..' is clamped within the source dir so '../keep' resolves to "
      "<source>/keep and cannot reach a sibling of the source")

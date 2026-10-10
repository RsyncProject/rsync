#!/usr/bin/env python3

import os
import subprocess

from harness.rsync import SCRATCHDIR, rmtree, rsync_argv, test_fail

base = SCRATCHDIR / 'keep-dirlinks'
rmtree(base)
base.mkdir(parents=True)

def dest_with_symlink(name):
    d = base / name
    rmtree(d)
    (d / 'target').mkdir(parents=True)
    os.symlink('target', d / 'd')
    return d

def run(src, dst, *opts):
    subprocess.run(rsync_argv('-a', *opts, f'{src}/', f'{dst}/'),
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

sdir = base / 'src_dir'
(sdir / 'd').mkdir(parents=True)
(sdir / 'd' / 'f').write_text('X\n')

sfile = base / 'src_file'
sfile.mkdir()
(sfile / 'd').write_text('FILE\n')

d = dest_with_symlink('case_match')
run(sdir, d, '--keep-dirlinks')
if not (d / 'd').is_symlink():
    test_fail("--keep-dirlinks: dest symlink-to-dir was replaced despite a sender dir match")
through = d / 'target' / 'f'
if not through.is_file() or through.read_text() != 'X\n':
    test_fail("--keep-dirlinks: contents did not land THROUGH the kept symlink")

d = dest_with_symlink('case_file')
run(sfile, d, '--keep-dirlinks')
if (d / 'd').is_symlink():
    test_fail("--keep-dirlinks: dest symlink kept even though the sender 'd' is a file")
if not (d / 'd').is_file() or (d / 'd').read_text() != 'FILE\n':
    test_fail("--keep-dirlinks: sender file did not replace the dest symlink")

d = dest_with_symlink('case_default')
run(sdir, d)
if (d / 'd').is_symlink():
    test_fail("default: dest symlink-to-dir should have been replaced by a real dir")
if not (d / 'd').is_dir() or not (d / 'd' / 'f').is_file():
    test_fail("default: 'd' was not recreated as a real directory with the file")

print("keep-dirlinks-rule: -K keeps+follows a dest symlink-to-dir only on a "
      "sender DIR match (contents land through it); a sender file replaces it; "
      "the default replaces the symlink with a real directory")

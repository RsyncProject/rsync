#!/usr/bin/env python3

import os
import subprocess

from harness.rsync import SCRATCHDIR, forced_protocol, rmtree, rsync_argv, test_fail

proto = forced_protocol()
if proto is not None and proto < 30:
    print(f"no-implied-dirs-symlink: protocol {proto} -- the -R /./ implied-dir "
          "path element is rejected by the proto-29 generator; nothing to test")
    raise SystemExit(0)

base = SCRATCHDIR / 'no-implied-dirs'
rmtree(base)
base.mkdir(parents=True)

src = base / 'src'
(src / 'path').mkdir(parents=True)
(src / 'path' / 'file').write_text('NEW\n')

def run(dst, *opts):
    rmtree(dst)
    (dst / 'real').mkdir(parents=True)
    os.symlink('real', dst / 'path')
    subprocess.run(rsync_argv('-a', '-R', *opts, f'{src}/./path/file', f'{dst}/'),
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return dst

d = run(base / 'noimplied', '--no-implied-dirs')
if not (d / 'path').is_symlink():
    test_fail("--no-implied-dirs: dest 'path' symlink was replaced (expected kept)")
through = d / 'real' / 'file'
if not through.is_file() or through.read_text() != 'NEW\n':
    test_fail("--no-implied-dirs: write was not redirected through the symlink "
              "(expected real/file to receive the content)")

d = run(base / 'implied')
if (d / 'path').is_symlink():
    test_fail("default: dest 'path' symlink should have been replaced by a real dir")
direct = d / 'path' / 'file'
if not direct.is_file() or direct.read_text() != 'NEW\n':
    test_fail("default: file did not land in the recreated real directory")

print("no-implied-dirs-symlink: -R --no-implied-dirs FOLLOWS an existing dest "
      "symlink path element (write redirected through it); the default replaces "
      "the symlink with a real directory")

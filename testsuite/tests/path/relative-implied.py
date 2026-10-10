#!/usr/bin/env python3

import os

from harness.rsync import (
    SCRATCHDIR, TODIR,
    assert_mode, assert_same, forced_protocol, makepath, rmtree, run_rsync,
)

os.umask(0o022)

base = SCRATCHDIR / 'rbase'
rmtree(base)
rmtree(TODIR)
makepath(base / 'a' / 'b' / 'c')
os.chmod(base / 'a' / 'b', 0o750)
(base / 'a' / 'b' / 'c' / 'file').write_text("data\n")

os.chdir(base / 'a')

run_rsync('-aR', 'b/c/file', f'{TODIR}/')
assert_mode(TODIR / 'b', 0o750, label='-R mirrors implied-dir mode')
assert_same(TODIR / 'b' / 'c' / 'file', base / 'a' / 'b' / 'c' / 'file',
            label='-R deep file')

proto = forced_protocol()
if proto is not None and proto < 30:
    print(f"relative-implied: protocol {proto} -- skipping --no-implied-dirs "
          "(the multi-component path is rejected by the proto-29 generator)")
else:
    rmtree(TODIR)
    run_rsync('-aR', '--no-implied-dirs', 'b/c/file', f'{TODIR}/')
    assert_mode(TODIR / 'b', 0o755,
                label='--no-implied-dirs uses the default mode, not source 0750')
    assert_same(TODIR / 'b' / 'c' / 'file', base / 'a' / 'b' / 'c' / 'file',
                label='--no-implied-dirs deep file')

print("relative-implied: -R mirrors implied-dir attrs; --no-implied-dirs does not")

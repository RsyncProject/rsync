#!/usr/bin/env python3
"""Coverage of -R implied directories and --no-implied-dirs at depth.

With -R the directories implied by a source path are recreated at the
destination AND have their attributes mirrored from the source. With
--no-implied-dirs those implied directories are still created as needed but get
default attributes instead of the source's. Verify the distinction on an
implied directory carrying a non-default mode, several levels deep.
"""

import os

from rsyncfns import (
    SCRATCHDIR, TODIR,
    assert_mode, assert_same, forced_protocol, makepath, rmtree, run_rsync,
)

os.umask(0o022)        # make the default implied-dir mode deterministic (0o755)

base = SCRATCHDIR / 'rbase'
rmtree(base)
rmtree(TODIR)
makepath(base / 'a' / 'b' / 'c')
os.chmod(base / 'a' / 'b', 0o750)         # distinctive mode on an implied dir
(base / 'a' / 'b' / 'c' / 'file').write_text("data\n")

os.chdir(base / 'a')

# -R: implied dirs b and c are recreated with the source's attributes.
run_rsync('-aR', 'b/c/file', f'{TODIR}/')
assert_mode(TODIR / 'b', 0o750, label='-R mirrors implied-dir mode')
assert_same(TODIR / 'b' / 'c' / 'file', base / 'a' / 'b' / 'c' / 'file',
            label='-R deep file')

# --no-implied-dirs: implied dir b is created with default (not source) attrs.
# At protocol 29 the generator rejects a multi-component path that has no
# transmitted directory entries ("invalid path from sender"), so this half is
# protocol-30+.
proto = forced_protocol()
if proto is not None and proto < 30:
    print(f"relative-implied: protocol {proto} -- skipping --no-implied-dirs "
          "(the multi-component path is rejected by the proto-29 generator)")
else:
    rmtree(TODIR)
    run_rsync('-aR', '--no-implied-dirs', 'b/c/file', f'{TODIR}/')
    # The implied dir must get the default mode (0o777 & ~umask == 0o755),
    # not the source's distinctive 0750 -- assert the exact mode, not merely
    # "different from 0750".
    assert_mode(TODIR / 'b', 0o755,
                label='--no-implied-dirs uses the default mode, not source 0750')
    assert_same(TODIR / 'b' / 'c' / 'file', base / 'a' / 'b' / 'c' / 'file',
                label='--no-implied-dirs deep file')

print("relative-implied: -R mirrors implied-dir attrs; --no-implied-dirs does not")

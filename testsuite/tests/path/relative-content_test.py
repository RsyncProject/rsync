#!/usr/bin/env python3
# rsync -aR (--relative) must transfer file CONTENT, not just the directory tree.
#
# Regression for a 3.5.0-dev sender bug: the symlink-race-confined content open
# became secure_relative_open(NULL, fname, O_RDONLY|O_NOFOLLOW, 0), and
# secure_relative_open() rejects an ABSOLUTE relpath with EINVAL before opening.
# With --relative the sender keeps the full (absolute) source path as fname, so
# every regular file failed to open: directories/symlinks still transferred, but
# the files arrived empty/missing and the run exited 23 ("send_files failed to
# open ...: Invalid argument (22)").  A near-silent data-loss bug for the very
# common "rsync -aR /abs/path backup/" pattern.  3.4.x was unaffected.
#
# Plain transfer, no root or special features -- this must pass everywhere.

from pathlib import Path

from rsyncfns import SCRATCHDIR, makepath, rmtree, run_rsync, test_fail

base = SCRATCHDIR / 'relcontent'
src = base / 'src' / 'sub'
dest = base / 'dest'
rmtree(base)
makepath(src, dest)

payload = 'hello relative content\n'
(src / 'file').write_text(payload)

# --relative with an ABSOLUTE source path: fname on the sender is absolute, the
# exact trigger.  check=True fails the test if rsync exits non-zero (the buggy
# sender exits 23 here).
abs_file = str((src / 'file').resolve())
run_rsync('-aR', abs_file, str(dest) + '/')

# -R mirrors the absolute source path under dest: dest/<abs_file without leading />.
landed = dest / abs_file.lstrip('/')
if not landed.is_file():
    test_fail(f"rsync -aR did not create {landed} (only the tree, no file?)")
got = landed.read_text()
if got != payload:
    test_fail(f"rsync -aR transferred empty/wrong content: {got!r} != {payload!r}")

print("relative-content: rsync -aR transfers file content for an absolute source")

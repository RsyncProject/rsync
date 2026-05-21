#!/usr/bin/env python3
# Python rewrite of testsuite/mkpath.test.
#
# Test the rsync --mkpath option: it should create any missing intermediate
# destination directories rather than erroring out.

import os
import shutil
from pathlib import Path

from rsyncfns import (
    FROMDIR, SRCDIR, TMPDIR, TODIR,
    makepath, rmtree, run_rsync, test_fail,
)


makepath(FROMDIR, TODIR)
shutil.copy2(SRCDIR / 'rsync.h', FROMDIR / 'text')
shutil.copy2(SRCDIR / 'configure.ac', FROMDIR / 'extra')

# All paths in the rsync invocations below are interpreted relative to
# TMPDIR, matching the original shell test which did `cd "$tmpdir"`.
os.chdir(TMPDIR)

deep_dir = Path('to/foo/bar/baz/down/deep')


def assert_file(path: Path, label: str) -> None:
    if not path.is_file():
        test_fail(f"{label}: {path} not found")


# Create several levels of dest dir (file destination — final component
# is the new filename).
run_rsync('-aiv', '--mkpath', 'from/text', str(deep_dir / 'new'))
assert_file(deep_dir / 'new', "'new' file in deep dir")
rmtree('to/foo')

# Trailing slash on the dest means it's a directory; the file keeps its name.
run_rsync('-aiv', '--mkpath', 'from/text', str(deep_dir) + '/')
assert_file(deep_dir / 'text', "'text' file in deep dir (trailing-slash dest)")
(deep_dir / 'text').unlink()

# An existing destination directory should also work.
(deep_dir / 'new').mkdir(parents=True, exist_ok=True)
run_rsync('-aiv', '--mkpath', 'from/text', str(deep_dir / 'new'))
assert_file(deep_dir / 'new' / 'text', "'text' file in pre-existing deep/new dir")

# ... and an existing path when an alternate dest filename is specified.
run_rsync('-aiv', '--mkpath', 'from/text', str(deep_dir / 'new' / 'text2'))
assert_file(deep_dir / 'new' / 'text2', "'text2' renamed file in pre-existing deep/new dir")
rmtree('to/foo')

# Multiple source args (whole directory) — bare dest name.
run_rsync('-aiv', '--mkpath', 'from/', str(deep_dir))
assert_file(deep_dir / 'extra', "'extra' file in deep dir (multi-source, no trailing slash)")
rmtree('to/foo')

# Multiple source args (whole directory) — dest with trailing slash.
run_rsync('-aiv', '--mkpath', 'from/', str(deep_dir) + '/')
assert_file(deep_dir / 'text', "'text' file in deep dir (multi-source, trailing slash)")

# No intermediate path at all — dest is just a file in the current dir.
run_rsync('-aiv', '--mkpath', 'from/text', 'to_text')
assert_file(Path('to_text'), "'to_text' file in current dir")

#!/usr/bin/env python3

import filecmp
import os
import shutil
from pathlib import Path

from harness.rsync import (
    FROMDIR, SRCDIR, TMPDIR, TODIR,
    makepath, rmtree, run_rsync, test_fail,
)

makepath(FROMDIR, TODIR)
shutil.copy2(SRCDIR / 'rsync.h', FROMDIR / 'text')
shutil.copy2(SRCDIR / 'configure.ac', FROMDIR / 'extra')

os.chdir(TMPDIR)

deep_dir = Path('to/foo/bar/baz/down/deep')

def assert_file(path: Path, label: str, src: str = 'from/text') -> None:
    if not path.is_file():
        test_fail(f"{label}: {path} not found")
    if not filecmp.cmp(path, src, shallow=False):
        test_fail(f"{label}: {path} content differs from {src}")

rmtree('to/foo')
proc = run_rsync('-ai', 'from/text', str(deep_dir / 'new'), check=False)
if proc.returncode == 0 or (deep_dir / 'new').exists():
    test_fail("a transfer WITHOUT --mkpath created the missing intermediate path")

run_rsync('-aiv', '--mkpath', 'from/text', str(deep_dir / 'new'))
assert_file(deep_dir / 'new', "'new' file in deep dir")
rmtree('to/foo')

run_rsync('-aiv', '--mkpath', 'from/text', str(deep_dir) + '/')
assert_file(deep_dir / 'text', "'text' file in deep dir (trailing-slash dest)")
(deep_dir / 'text').unlink()

(deep_dir / 'new').mkdir(parents=True, exist_ok=True)
run_rsync('-aiv', '--mkpath', 'from/text', str(deep_dir / 'new'))
assert_file(deep_dir / 'new' / 'text', "'text' file in pre-existing deep/new dir")

run_rsync('-aiv', '--mkpath', 'from/text', str(deep_dir / 'new' / 'text2'))
assert_file(deep_dir / 'new' / 'text2', "'text2' renamed file in pre-existing deep/new dir")
rmtree('to/foo')

run_rsync('-aiv', '--mkpath', 'from/', str(deep_dir))
assert_file(deep_dir / 'extra', "'extra' file in deep dir (multi-source, no trailing slash)",
            src='from/extra')
rmtree('to/foo')

run_rsync('-aiv', '--mkpath', 'from/', str(deep_dir) + '/')
assert_file(deep_dir / 'text', "'text' file in deep dir (multi-source, trailing slash)")

run_rsync('-aiv', '--mkpath', 'from/text', 'to_text')
assert_file(Path('to_text'), "'to_text' file in current dir")

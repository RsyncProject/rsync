#!/usr/bin/env python3

from fixtures.trees import build_symlinks
from harness.environment import FROMDIR, TODIR
from harness.results import test_fail
from harness.rsync import run_rsync
from harness import metadata

metadata(transports={'pipe'})

build_symlinks()

run_rsync('-r', f'{FROMDIR}/', str(TODIR))

if not (TODIR / 'referent').is_file():
    test_fail("referent was not copied")
if (TODIR / 'from').is_dir():
    test_fail("extra level of directories")
if (TODIR / 'dangling').is_symlink():
    test_fail("dangling symlink was copied")
if (TODIR / 'relative').is_symlink():
    test_fail("relative symlink was copied")
if (TODIR / 'absolute').is_symlink():
    test_fail("absolute symlink was copied")

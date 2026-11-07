#!/usr/bin/env python3

from harness.rsync import (
    FROMDIR, TODIR,
    build_symlinks, is_a_link, run_rsync, test_fail,
)

build_symlinks()

run_rsync('-r', f'{FROMDIR}/', str(TODIR))

if not (TODIR / 'referent').is_file():
    test_fail("referent was not copied")
if (TODIR / 'from').is_dir():
    test_fail("extra level of directories")
if is_a_link(TODIR / 'dangling'):
    test_fail("dangling symlink was copied")
if is_a_link(TODIR / 'relative'):
    test_fail("relative symlink was copied")
if is_a_link(TODIR / 'absolute'):
    test_fail("absolute symlink was copied")

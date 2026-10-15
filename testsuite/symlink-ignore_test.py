#!/usr/bin/env python3
# Python rewrite of testsuite/symlink-ignore.test.
#
# Default behaviour: without -l, -L or -a, rsync should NOT copy any
# symlinks. The referent file should land in the destination but every
# symlink in the source must be absent.

from rsyncfns import (
    FROMDIR, TODIR,
    build_symlinks, is_a_link, run_rsync, test_fail,
)


build_symlinks()

# Copy recursively, but without -l or -L or -a, so symlinks should be missing.
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

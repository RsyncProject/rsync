#!/usr/bin/env python3
# Python rewrite of testsuite/safe-links.test.
#
# --safe-links must drop symlinks whose target escapes the transfer root.
# In-tree symlinks survive; escape-attempt symlinks (../../..., a/a/../../...)
# must NOT appear at the destination at all.

import os

from rsyncfns import TMPDIR, is_a_link, run_rsync, test_fail


def assert_symlink(path, target):
    if not is_a_link(path):
        test_fail(f"File {path} is not a symlink")
    actual = os.readlink(path)
    if actual != target:
        test_fail(f"symlink {path} target is {actual!r}, expected {target!r}")


def assert_notexist(path):
    if os.path.exists(path):
        test_fail(f"File {path} exists")
    if os.path.islink(path):
        test_fail(f"File {path} exists as a symlink")


os.chdir(TMPDIR)

os.mkdir("from")
os.mkdir("from/safe")
os.mkdir("from/unsafe")
os.mkdir("from/safe/files")
os.mkdir("from/safe/links")

open("from/safe/files/file1", "w").close()
open("from/safe/files/file2", "w").close()
open("from/unsafe/unsafefile", "w").close()

os.symlink("../files/file1", "from/safe/links/file1")
os.symlink("../files/file2", "from/safe/links/file2")
os.symlink("../../unsafe/unsafefile", "from/safe/links/unsafefile")
os.symlink("a/a/a/../../../unsafe2", "from/safe/links/unsafe2")

print("rsync with relative path and --safe-links")
run_rsync('-avv', '--safe-links', 'from/safe/', 'to')

assert_symlink("to/links/file1", "../files/file1")
assert_symlink("to/links/file2", "../files/file2")
assert_notexist("to/links/unsafefile")
assert_notexist("to/links/unsafe2")

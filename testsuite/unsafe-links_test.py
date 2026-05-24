#!/usr/bin/env python3
# Python rewrite of testsuite/unsafe-links.test.
#
# Verifies the three relevant policies for "unsafe" (escape-the-tree) symlinks:
#   * default -a copies them as symlinks (no special handling),
#   * --copy-links materialises ALL symlinks as files,
#   * --copy-unsafe-links materialises only the unsafe ones, leaving safe
#     in-tree symlinks as symlinks.

import os

from rsyncfns import TMPDIR, is_a_link, rmtree, run_rsync, test_fail


def assert_symlink(path, target):
    if not is_a_link(path):
        test_fail(f"File {path} is not a symlink")
    actual = os.readlink(path)
    if actual != target:
        test_fail(f"symlink {path} target is {actual!r}, expected {target!r}")


def assert_regular(path):
    if not os.path.isfile(path):
        test_fail(f"File {path} is not regular file or not exists")


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


print("rsync with relative path and just -a")
run_rsync('-avv', 'from/safe/', 'to')
assert_symlink("to/links/file1", "../files/file1")
assert_symlink("to/links/file2", "../files/file2")
assert_symlink("to/links/unsafefile", "../../unsafe/unsafefile")

print("rsync with relative path and -a --copy-links")
run_rsync('-avv', '--copy-links', 'from/safe/', 'to')
assert_regular("to/links/file1")
assert_regular("to/links/file2")
assert_regular("to/links/unsafefile")

print("rsync with relative path and --copy-unsafe-links")
run_rsync('-avv', '--copy-unsafe-links', 'from/safe/', 'to')
assert_symlink("to/links/file1", "../files/file1")
assert_symlink("to/links/file2", "../files/file2")
assert_regular("to/links/unsafefile")

rmtree("to")
print("rsync with relative2 path")
# Mirror the shell `(cd from; rsync ... safe/ ../to)` subshell — chdir, rsync,
# then chdir back so the final block uses an absolute source again.
saved = os.getcwd()
os.chdir("from")
try:
    run_rsync('-avv', '--copy-unsafe-links', 'safe/', '../to')
finally:
    os.chdir(saved)
assert_symlink("to/links/file1", "../files/file1")
assert_symlink("to/links/file2", "../files/file2")
assert_regular("to/links/unsafefile")

rmtree("to")
print("rsync with absolute path")
run_rsync('-avv', '--copy-unsafe-links', f'{os.getcwd()}/from/safe/', 'to')
assert_symlink("to/links/file1", "../files/file1")
assert_symlink("to/links/file2", "../files/file2")
assert_regular("to/links/unsafefile")

#!/usr/bin/env python3
# Absolute symlink that *resolves inside* the copied tree, under --safe-links.
#
# This is the case that surprises users: a symlink and its referent live in
# the same source directory, so the link "obviously" stays inside the transfer
# -- yet --safe-links still drops it.  The reason is that rsync classifies a
# link's safety from the *literal text* of its target, never by resolving it.
# An absolute target (one starting with '/') is unconditionally "unsafe",
# regardless of where it actually points.  See unsafe_symlink() in util1.c
# ("all absolute and null symlinks are unsafe") and the SYMBOLIC LINKS section
# of the man page ("considered unsafe if they are absolute symlinks").
#
# The same link written as a *relative* path is safe and survives, which is
# the recommended fix.

import os

from rsyncfns import (
    TMPDIR, is_a_link, run_rsync, test_fail,
)


def assert_symlink(path, target):
    if not is_a_link(path):
        test_fail(f"File {path} is not a symlink")
    actual = os.readlink(path)
    if actual != target:
        test_fail(f"symlink {path} target is {actual!r}, expected {target!r}")


def assert_notexist(path):
    # os.path.exists() follows the link, so a dropped link reads as "missing";
    # islink() catches a link that was copied verbatim but left dangling.
    if os.path.exists(path) or os.path.islink(path):
        test_fail(f"File {path} unexpectedly exists")


def assert_regular_file(path):
    if is_a_link(path):
        test_fail(f"File {path} is a symlink, expected a regular file")
    if not os.path.isfile(path):
        test_fail(f"File {path} is not a regular file")


os.chdir(TMPDIR)

os.mkdir("from")
with open("from/linked_file", "w") as f:
    f.write("payload\n")

# Both links point at the very same in-tree file; only the spelling differs.
abs_target = os.path.abspath("from/linked_file")
os.symlink(abs_target, "from/abs_link")      # absolute -> always "unsafe"
os.symlink("linked_file", "from/rel_link")   # relative, same dir -> "safe"

# Sanity: the absolute link really does resolve to the in-tree file.
if os.path.realpath("from/abs_link") != os.path.realpath("from/linked_file"):
    test_fail("test setup: abs_link does not resolve to linked_file")

# --- 1. Baseline: plain -a (no --safe-links) keeps the absolute link as-is. --
print("baseline: -a without --safe-links preserves the absolute symlink")
run_rsync('-a', 'from/', 'to-plain')
assert_symlink("to-plain/abs_link", abs_target)
assert_symlink("to-plain/rel_link", "linked_file")

# --- 2. --safe-links drops the absolute link though it resolves in-tree. -----
print("--safe-links drops the in-tree-resolving absolute symlink")
proc = run_rsync('-av', '--safe-links', 'from/', 'to-safe',
                 capture_output=True)
out = proc.stdout + proc.stderr
if 'ignoring unsafe symlink' not in out:
    test_fail(f"expected 'ignoring unsafe symlink' message, got:\n{out}")

# The absolute link is omitted entirely -- NOT replaced by its target file.
assert_notexist("to-safe/abs_link")
# The relative link to the same file survives untouched.
assert_symlink("to-safe/rel_link", "linked_file")
# The referent itself is still copied normally.
assert_regular_file("to-safe/linked_file")

# --- 3. The fix paths. -------------------------------------------------------
# --copy-unsafe-links turns the unsafe (absolute) link into a real file copy.
print("--copy-unsafe-links materialises the absolute link as a file")
run_rsync('-a', '--copy-unsafe-links', 'from/', 'to-copy')
assert_regular_file("to-copy/abs_link")
assert_symlink("to-copy/rel_link", "linked_file")

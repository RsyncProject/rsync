#!/usr/bin/env python3
# Python rewrite of testsuite/symlink-dirlink-basis.test.
#
# Regression test for https://github.com/RsyncProject/rsync/issues/715:
# updating a file through a directory symlink with -K (--copy-dirlinks)
# regressed after the CVE-2026-29518 fix introduced
# secure_relative_open() with O_NOFOLLOW on every path component. The
# fix split the path so basedir (dirname) follows symlinks while only
# the final component is O_NOFOLLOW'd.
#
# We exercise: basic dir-symlink update, compressed update, nested
# dir-symlinks, --backup, --inplace, top-level file (no split needed),
# --partial-dir with protocol < 29, and a basic protocol < 29 transfer.

import filecmp
import os
import platform
import subprocess
import time

from rsyncfns import (
    RSYNC, SCRATCHDIR, SRCDIR, TMPDIR,
    make_data_file, rsync_argv, test_fail, test_skipped,
)


if platform.system() in ('SunOS', 'OpenBSD', 'NetBSD') or platform.system().startswith('CYGWIN'):
    test_skipped(
        f"secure_relative_open lacks RESOLVE_BENEATH equivalent on "
        f"{platform.system()}; issue #715 still affects this platform"
    )

os.environ['RSYNC_RSH'] = str(SRCDIR / 'support' / 'lsh.sh')
# HOME -> SCRATCHDIR is set up by rsyncfns import.

srcbase = TMPDIR / 'src_files'  # avoid clash with the runner's $scratchdir/src symlink
srcbase.mkdir(parents=True, exist_ok=True)
home = SCRATCHDIR


def make_testfile(path) -> None:
    """~32 KiB of non-trivial content -- large enough to trigger rsync's
    block-matching delta path."""
    make_data_file(path, 32768)


def push(*args, label: str) -> None:
    # --rsync-path goes BEFORE the positional source/dest args, matching
    # the shell test's order. With it at the end rsync mis-classifies
    # the destination and reports "Unexpected remote arg: localhost:".
    saved = os.getcwd()
    os.chdir(srcbase)
    try:
        proc = subprocess.run(
            rsync_argv(f'--rsync-path={RSYNC}', *args),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        print(proc.stdout, end='')
        if proc.returncode != 0:
            test_fail(f"{label}: rsync exited {proc.returncode}")
    finally:
        os.chdir(saved)


def assert_same(label: str, a, b) -> None:
    if not filecmp.cmp(a, b, shallow=False):
        test_fail(f"{label}: content mismatch between {a} and {b}")


# Test 1: basic directory-symlink update.
(home / 'real-dir').mkdir()
os.symlink('real-dir', home / 'dir')
(srcbase / 'dir').mkdir()
make_testfile(srcbase / 'dir' / 'file')

push('-KRlptv', 'dir/file', 'localhost:', label="test 1 initial")
if not (home / 'real-dir' / 'file').is_file():
    test_fail("test 1: initial transfer did not create file through symlink")
assert_same("test 1 initial", srcbase / 'dir' / 'file', home / 'real-dir' / 'file')

# Trigger delta transfer.
with open(srcbase / 'dir' / 'file', 'ab') as f:
    f.write(b"appended update\n")
time.sleep(1)
(srcbase / 'dir' / 'file').touch()

push('-KRlptv', 'dir/file', 'localhost:', label="test 1 update")
assert_same("test 1 update", srcbase / 'dir' / 'file', home / 'real-dir' / 'file')


# Test 2: compression.
with open(srcbase / 'dir' / 'file', 'ab') as f:
    f.write(b"another line\n")
time.sleep(1)
(srcbase / 'dir' / 'file').touch()

push('-KRlptzv', 'dir/file', 'localhost:', label="test 2")
assert_same("test 2", srcbase / 'dir' / 'file', home / 'real-dir' / 'file')


# Test 3: nested directory symlinks.
(home / 'nested_real' / 'sub').mkdir(parents=True)
os.symlink('nested_real', home / 'nested')

(srcbase / 'nested' / 'sub').mkdir(parents=True)
make_testfile(srcbase / 'nested' / 'sub' / 'data.txt')

push('-KRlptv', 'nested/sub/data.txt', 'localhost:', label="test 3 initial")

with open(srcbase / 'nested' / 'sub' / 'data.txt', 'ab') as f:
    f.write(b"appended nested\n")
time.sleep(1)
(srcbase / 'nested' / 'sub' / 'data.txt').touch()

push('-KRlptv', 'nested/sub/data.txt', 'localhost:', label="test 3 update")
assert_same("test 3 update",
            srcbase / 'nested' / 'sub' / 'data.txt',
            home / 'nested_real' / 'sub' / 'data.txt')


# Test 4: --backup with directory symlinks.
(home / 'real-dir' / 'file').unlink()
(home / 'real-dir' / 'file~').unlink(missing_ok=True)
make_testfile(srcbase / 'dir' / 'file')

push('-KRlptv', 'dir/file', 'localhost:', label="test 4 initial")

with open(srcbase / 'dir' / 'file', 'ab') as f:
    f.write(b"backup update\n")
time.sleep(1)
(srcbase / 'dir' / 'file').touch()

push('-KRlptv', '--backup', 'dir/file', 'localhost:', label="test 4 update")
assert_same("test 4 update", srcbase / 'dir' / 'file', home / 'real-dir' / 'file')
if not (home / 'real-dir' / 'file~').is_file():
    test_fail("test 4: backup file was not created")


# Test 5: --inplace.
(home / 'real-dir' / 'file').unlink()
(home / 'real-dir' / 'file~').unlink(missing_ok=True)
make_testfile(srcbase / 'dir' / 'file')

push('-KRlptv', '--inplace', 'dir/file', 'localhost:', label="test 5 initial")

with open(srcbase / 'dir' / 'file', 'ab') as f:
    f.write(b"inplace update\n")
time.sleep(1)
(srcbase / 'dir' / 'file').touch()

push('-KRlptv', '--inplace', 'dir/file', 'localhost:', label="test 5 update")
assert_same("test 5 update", srcbase / 'dir' / 'file', home / 'real-dir' / 'file')


# Test 6: top-level file (no dirname split needed).
make_testfile(srcbase / 'topfile')
home.mkdir(parents=True, exist_ok=True)

push('-Rlptv', 'topfile', 'localhost:', label="test 6 initial")

with open(srcbase / 'topfile', 'ab') as f:
    f.write(b"toplevel update\n")
time.sleep(1)
(srcbase / 'topfile').touch()

push('-Rlptv', 'topfile', 'localhost:', label="test 6 update")
assert_same("test 6 update", srcbase / 'topfile', home / 'topfile')


# Test 7: --partial-dir with protocol < 29.
(home / 'real-dir' / 'file').unlink(missing_ok=True)
make_testfile(srcbase / 'dir' / 'file')

push('-KRlptv', '--protocol=28', '--partial-dir=.rsync-partial',
     'dir/file', 'localhost:', label="test 7 initial")

with open(srcbase / 'dir' / 'file', 'ab') as f:
    f.write(b"partial-dir update\n")
time.sleep(1)
(srcbase / 'dir' / 'file').touch()

push('-KRlptv', '--protocol=28', '--partial-dir=.rsync-partial',
     'dir/file', 'localhost:', label="test 7 update")
assert_same("test 7 update", srcbase / 'dir' / 'file', home / 'real-dir' / 'file')


# Test 8: protocol < 29 basic update.
(home / 'real-dir' / 'file').unlink(missing_ok=True)
make_testfile(srcbase / 'dir' / 'file')

push('-KRlptv', '--protocol=28', 'dir/file', 'localhost:', label="test 8 initial")

with open(srcbase / 'dir' / 'file', 'ab') as f:
    f.write(b"proto28 update\n")
time.sleep(1)
(srcbase / 'dir' / 'file').touch()

push('-KRlptv', '--protocol=28', 'dir/file', 'localhost:', label="test 8 update")
assert_same("test 8 update", srcbase / 'dir' / 'file', home / 'real-dir' / 'file')

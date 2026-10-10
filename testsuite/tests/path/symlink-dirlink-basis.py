#!/usr/bin/env python3

import filecmp
import os
import subprocess

from harness.rsync import (
    SCRATCHDIR,
    TMPDIR,
    make_data_file,
    rsync_argv,
    test_fail,
    rsync_path_arg,
    rsh_cmd,
)
from harness import metadata

metadata(features={'remote-shell', 'symlink'}, transports={'pipe', 'tcp'}, min_peer='2.6.0', mutates={'filesystem', 'process'}, tags={'compatibility', 'security', 'transfer', 'version-mix'})

os.environ['RSYNC_RSH'] = rsh_cmd()

srcbase = TMPDIR / 'src_files'
srcbase.mkdir(parents=True, exist_ok=True)
home = SCRATCHDIR

def make_testfile(path) -> None:
    make_data_file(path, 32768)

def advance_mtime(path) -> None:
    st = path.stat()
    os.utime(path, (st.st_atime, st.st_mtime + 10))

def push(*args, label: str) -> None:
    saved = os.getcwd()
    os.chdir(srcbase)
    try:
        proc = subprocess.run(
            rsync_argv(f'--rsync-path={rsync_path_arg()}', *args),
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

(home / 'real-dir').mkdir()
os.symlink('real-dir', home / 'dir')
(srcbase / 'dir').mkdir()
make_testfile(srcbase / 'dir' / 'file')

push('-KRlptv', 'dir/file', 'localhost:', label="test 1 initial")
if not (home / 'real-dir' / 'file').is_file():
    test_fail("test 1: initial transfer did not create file through symlink")
assert_same("test 1 initial", srcbase / 'dir' / 'file', home / 'real-dir' / 'file')

with open(srcbase / 'dir' / 'file', 'ab') as f:
    f.write(b"appended update\n")
advance_mtime(srcbase / 'dir' / 'file')

push('-KRlptv', 'dir/file', 'localhost:', label="test 1 update")
assert_same("test 1 update", srcbase / 'dir' / 'file', home / 'real-dir' / 'file')

with open(srcbase / 'dir' / 'file', 'ab') as f:
    f.write(b"another line\n")
advance_mtime(srcbase / 'dir' / 'file')

push('-KRlptzv', 'dir/file', 'localhost:', label="test 2")
assert_same("test 2", srcbase / 'dir' / 'file', home / 'real-dir' / 'file')

(home / 'nested_real' / 'sub').mkdir(parents=True)
os.symlink('nested_real', home / 'nested')

(srcbase / 'nested' / 'sub').mkdir(parents=True)
make_testfile(srcbase / 'nested' / 'sub' / 'data.txt')

push('-KRlptv', 'nested/sub/data.txt', 'localhost:', label="test 3 initial")

with open(srcbase / 'nested' / 'sub' / 'data.txt', 'ab') as f:
    f.write(b"appended nested\n")
advance_mtime(srcbase / 'nested' / 'sub' / 'data.txt')

push('-KRlptv', 'nested/sub/data.txt', 'localhost:', label="test 3 update")
assert_same("test 3 update",
            srcbase / 'nested' / 'sub' / 'data.txt',
            home / 'nested_real' / 'sub' / 'data.txt')

(home / 'real-dir' / 'file').unlink()
(home / 'real-dir' / 'file~').unlink(missing_ok=True)
make_testfile(srcbase / 'dir' / 'file')

push('-KRlptv', 'dir/file', 'localhost:', label="test 4 initial")

old_content = (srcbase / 'dir' / 'file').read_bytes()
with open(srcbase / 'dir' / 'file', 'ab') as f:
    f.write(b"backup update\n")
advance_mtime(srcbase / 'dir' / 'file')

push('-KRlptv', '--backup', 'dir/file', 'localhost:', label="test 4 update")
assert_same("test 4 update", srcbase / 'dir' / 'file', home / 'real-dir' / 'file')
if not (home / 'real-dir' / 'file~').is_file():
    test_fail("test 4: backup file was not created")
if (home / 'real-dir' / 'file~').read_bytes() != old_content:
    test_fail("test 4: backup file~ does not hold the pre-update content")

(home / 'real-dir' / 'file').unlink()
(home / 'real-dir' / 'file~').unlink(missing_ok=True)
make_testfile(srcbase / 'dir' / 'file')

push('-KRlptv', '--inplace', 'dir/file', 'localhost:', label="test 5 initial")

with open(srcbase / 'dir' / 'file', 'ab') as f:
    f.write(b"inplace update\n")
advance_mtime(srcbase / 'dir' / 'file')

push('-KRlptv', '--inplace', 'dir/file', 'localhost:', label="test 5 update")
assert_same("test 5 update", srcbase / 'dir' / 'file', home / 'real-dir' / 'file')

make_testfile(srcbase / 'topfile')
home.mkdir(parents=True, exist_ok=True)

push('-Rlptv', 'topfile', 'localhost:', label="test 6 initial")

with open(srcbase / 'topfile', 'ab') as f:
    f.write(b"toplevel update\n")
advance_mtime(srcbase / 'topfile')

push('-Rlptv', 'topfile', 'localhost:', label="test 6 update")
assert_same("test 6 update", srcbase / 'topfile', home / 'topfile')

(home / 'real-dir' / 'file').unlink(missing_ok=True)
make_testfile(srcbase / 'dir' / 'file')

push('-KRlptv', '--protocol=28', '--partial-dir=.rsync-partial',
     'dir/file', 'localhost:', label="test 7 initial")

with open(srcbase / 'dir' / 'file', 'ab') as f:
    f.write(b"partial-dir update\n")
advance_mtime(srcbase / 'dir' / 'file')

push('-KRlptv', '--protocol=28', '--partial-dir=.rsync-partial',
     'dir/file', 'localhost:', label="test 7 update")
assert_same("test 7 update", srcbase / 'dir' / 'file', home / 'real-dir' / 'file')

(home / 'real-dir' / 'file').unlink(missing_ok=True)
make_testfile(srcbase / 'dir' / 'file')

push('-KRlptv', '--protocol=28', 'dir/file', 'localhost:', label="test 8 initial")

with open(srcbase / 'dir' / 'file', 'ab') as f:
    f.write(b"proto28 update\n")
advance_mtime(srcbase / 'dir' / 'file')

push('-KRlptv', '--protocol=28', 'dir/file', 'localhost:', label="test 8 update")
assert_same("test 8 update", srcbase / 'dir' / 'file', home / 'real-dir' / 'file')

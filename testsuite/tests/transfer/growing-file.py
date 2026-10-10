#!/usr/bin/env python3

import filecmp
import os
import subprocess
import threading
import time

from harness.rsync import SCRATCHDIR, make_data_file, rmtree, rsync_argv, test_fail

base = SCRATCHDIR / 'growing-file'
src = base / 'src'
dst = base / 'dst'
rmtree(base)
src.mkdir(parents=True)
dst.mkdir(parents=True)

make_data_file(src / 'aaa_big', 6 * 1024 * 1024)

grow = src / 'zzz_grow'
make_data_file(grow, 4096)
orig_size = grow.stat().st_size

grow_done = threading.Event()

def appender():
    time.sleep(0.6)
    with open(grow, 'ab') as fh:
        fh.write(os.urandom(256 * 1024))
        fh.flush()
        os.fsync(fh.fileno())
    grow_done.set()

t = threading.Thread(target=appender)
t.start()

proc = subprocess.run(
    rsync_argv('-a', '--no-inc-recursive', '--bwlimit=1500', f'{src}/', f'{dst}/'),
    capture_output=True, text=True)

t.join()

if not grow_done.is_set() or grow.stat().st_size <= orig_size:
    test_fail("test setup: source file did not grow during the transfer")

if proc.returncode != 0:
    if 'received more data than file length' in (proc.stdout + proc.stderr):
        test_fail(
            "rsync aborted the whole transfer with RERR_PROTOCOL when a file "
            "grew during the run (received more data than file length); it "
            "should tolerate the growth like stock rsync.\n"
            f"exit={proc.returncode}\n{proc.stdout}{proc.stderr}")
    test_fail(f"rsync exited {proc.returncode}\n{proc.stdout}{proc.stderr}")

if not (dst / 'zzz_grow').is_file():
    test_fail("the grown file was not transferred to the destination")
if not (dst / 'aaa_big').is_file():
    test_fail("the earlier (throttled) file was not transferred")

final_src = grow.stat().st_size
dst_size = (dst / 'zzz_grow').stat().st_size
if dst_size <= orig_size:
    test_fail(f"destination only got {dst_size} bytes (<= original {orig_size}); "
              "the growth-during-transfer path was not exercised")
if dst_size != final_src:
    test_fail(f"destination size {dst_size} != final source size {final_src}; "
              "the grown content was not fully transferred")
if not filecmp.cmp(str(grow), str(dst / 'zzz_grow'), shallow=False):
    test_fail("destination content does not match the grown source")

print("growing-file: a file appended to during the transfer no longer aborts "
      "the run; the grown file (and all later files) transfer intact")

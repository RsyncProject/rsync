#!/usr/bin/env python3
# Regression: a file that grows between file-list scan and data send must not
# abort the whole transfer.
#
# The sender records each file's length when it scans the file list, but re-
# stats and maps the file at its *current* size when it later sends the data
# (sender.c: do_fstat + map_file + match_sums use st.st_size).  A file that is
# appended to in that window -- e.g. a live log rotated/written during a nightly
# backup -- is therefore transmitted longer than its flist-recorded length.
#
# Stock rsync tolerates this: the receiver writes the grown content and the
# transfer completes.  A hardening check added to receive_data()
# (offset + i > total_size / offset + len > total_size) turned it into a fatal
# "received more data than file length" RERR_PROTOCOL, tearing down the whole
# connection so every file after the growing one is skipped.
#
# The receive_data() check is transport-agnostic (same code for a local, a
# remote-shell, and a daemon receiver), so this drives the simplest case: a
# local transfer with the growth injected during an earlier, throttled file.
#
# RED  (unfixed tree): rsync exits RERR_PROTOCOL (2) with "received more data
#                      than file length".
# GREEN (checks removed): rsync exits 0 and the grown file is copied.

import filecmp
import os
import subprocess
import threading
import time

from rsyncfns import SCRATCHDIR, make_data_file, rmtree, rsync_argv, test_fail

base = SCRATCHDIR / 'growing-file'
src = base / 'src'
dst = base / 'dst'
rmtree(base)
src.mkdir(parents=True)
dst.mkdir(parents=True)

# 'aaa_big' sorts first and is sent first; --bwlimit throttles it so the append
# to the later file lands well after the flist is built (--no-inc-recursive
# forces the whole flist up front) but well before that file is sent.
make_data_file(src / 'aaa_big', 6 * 1024 * 1024)

grow = src / 'zzz_grow'
make_data_file(grow, 4096)                 # flist records this small length
orig_size = grow.stat().st_size

grow_done = threading.Event()


def appender():
    # After the flist scan, during aaa_big's ~4s throttled transfer.
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

# Guard against a vacuous run: the file must actually have grown during the
# transfer, else we never exercised the scenario.
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

# Prove the scenario was actually exercised (not a vacuous pass): the sender
# transmitted the *grown* content (more than the flist-recorded orig_size), and
# the destination is a byte-for-byte copy of the final source.
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

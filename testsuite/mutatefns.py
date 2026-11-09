# Shared helper for "resource changes while being transferred" tests.
#
# rsync scans the file list first, then sends file data and applies metadata.
# A resource (content, size, perms, owner, mtime, xattr, ACL) mutated in that
# window must not abort the whole transfer or corrupt the destination -- rsync
# should transfer what it can and, at worst, warn.  The growing-file regression
# (a hardening check that turned a benign mid-transfer append into a fatal
# RERR_PROTOCOL) showed this whole class was untested.
#
# The reproduction is deterministic: a big 'aaa_pacer' file sorts first and is
# throttled with --bwlimit, so a later 'zzz_target' entry is sent well after the
# flist is built (--no-inc-recursive locks all sizes up front) but before its
# own data/metadata is processed.  A background thread performs the mutation in
# that window.

import os
import subprocess
import threading
import time

from rsyncfns import SCRATCHDIR, make_data_file, rmtree, rsync_argv, test_fail

TARGET = 'zzz_target'          # sorts after aaa_pacer -> processed second


def run_mutating_transfer(setup, mutate, *, extra_rsync=('-a',),
                          pacer_bytes=6 * 1024 * 1024, bwlimit=1500, delay=0.6):
    """Run a local rsync while `mutate` runs in the transfer window.

    setup(src_dir)   -- create the target resource(s); called before rsync.
    mutate(src_dir)  -- perform the mid-transfer change; called ~delay s in.
    extra_rsync      -- rsync flags (default -a).

    Returns (proc, src, dst).  Fails the test if the mutation never ran.
    """
    base = SCRATCHDIR / 'mutate'
    rmtree(base)
    src = base / 'src'
    dst = base / 'dst'
    src.mkdir(parents=True)
    dst.mkdir(parents=True)

    # The throttled pacer that keeps the transfer alive during the mutation.
    make_data_file(src / 'aaa_pacer', pacer_bytes)
    setup(src)

    done = threading.Event()
    err = {}

    def worker():
        time.sleep(delay)
        try:
            mutate(src)
        except Exception as e:                       # noqa: BLE001 - report it
            err['exc'] = e
        finally:
            done.set()

    t = threading.Thread(target=worker)
    t.start()
    proc = subprocess.run(
        rsync_argv(*extra_rsync, '--no-inc-recursive', f'--bwlimit={bwlimit}',
                   f'{src}/', f'{dst}/'),
        capture_output=True, text=True)
    t.join()

    if not done.is_set():
        test_fail("test setup: the mutation thread did not run")
    if 'exc' in err:
        test_fail(f"test setup: mutation raised {err['exc']!r}")
    return proc, src, dst


def assert_no_protocol_abort(proc):
    """rsync must not have torn the whole transfer down with a protocol error."""
    out = proc.stdout + proc.stderr
    if 'received more data than file length' in out:
        test_fail("regression: mid-transfer change aborted the run with "
                  f"'received more data than file length'\n{out}")
    if proc.returncode in (2,):  # RERR_PROTOCOL
        test_fail(f"regression: transfer aborted with RERR_PROTOCOL (code 2)\n{out}")
    # 0 = OK, 23/24 = partial/vanished (acceptable for a changing source).
    if proc.returncode not in (0, 23, 24):
        test_fail(f"unexpected rsync exit {proc.returncode}\n{out}")

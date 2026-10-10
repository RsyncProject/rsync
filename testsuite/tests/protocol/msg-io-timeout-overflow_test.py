#!/usr/bin/env python3
# A near-INT_MAX I/O timeout must not overflow set_io_timeout() into a tight CPU
# loop.  Reported by z3r0s (github z3r0s6), 2026-07.
#
# set_io_timeout() computed "allowed_lull = (io_timeout + 1) / 2", a signed
# overflow for io_timeout near INT_MAX that yields a negative allowed_lull /
# select_timeout; select() then returns EINVAL on the negative tv_sec, which
# isn't EBADF, so the read loop spins at 100% CPU forever (io_timeout ~= 68 years
# never fires check_timeout).  io_timeout reaches INT_MAX two ways, both fixed on
# this branch: a malicious daemon's MSG_IO_TIMEOUT (PR 50 caps the wire value at
# 86400) and an operator's --timeout (options.c parses it unbounded).  The root
# fix computes allowed_lull overflow-safe in a wider type in set_io_timeout(),
# covering both.
#
# Because "(io_timeout + 1)" is undefined behaviour, whether it manifests depends
# on the build: plain -O2 gcc/clang optimize the compare so select_timeout stays
# 60 (no spin, though allowed_lull is still stored negative), while -fwrapv /
# -fno-strict-overflow (common hardening) wrap deterministically to the negative
# select_timeout and the spin (-ftrapv aborts).  So to exercise the DoS
# deterministically on any target we build the rsync-under-test with -fwrapv and
# drive set_io_timeout(INT_MAX) through the --timeout path -- the same overflow a
# capped MSG_IO_TIMEOUT would otherwise reach.  (The wire path sets the timeout
# mid-stream, so whether the client next blocks in select() is timing-dependent;
# --timeout sets it at startup, so the very first select() spins -- deterministic.)
#
# Oracle: run the -fwrapv build with --timeout=INT_MAX on a normal local copy.
#   * transfer completes                  -> overflow-safe (PASS / GREEN)
#   * transfer runs past the deadline      -> tight select()-EINVAL loop (FAIL / RED)

import os
import signal
import subprocess

from rsyncfns import (
    SCRATCHDIR, build_patched_rsync, makepath, rmtree, test_fail,
)

INT_MAX = 2147483647
DEADLINE = 25          # a normal small copy finishes in well under a second

# -fwrapv build of the tree under test (no source patch); makes the signed
# overflow deterministic on every platform instead of compiler-optimized away.
victim = build_patched_rsync('io-timeout-fwrapv', [], append_cflags='-fwrapv')

base = SCRATCHDIR / 'io-timeout'
rmtree(base)
src = base / 'src'
dst = base / 'dst'
makepath(src)
makepath(dst)
(src / 'file').write_text("payload\n")

# --timeout=INT_MAX -> set_io_timeout(INT_MAX) at startup.  An unfixed -fwrapv
# build wraps select_timeout negative and the first select() spins forever; the
# fixed build keeps it sane and the copy completes.
proc = subprocess.Popen(
    [str(victim), '--timeout=%d' % INT_MAX, '-a', str(src) + '/', str(dst) + '/'],
    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    start_new_session=True)
try:
    out, _ = proc.communicate(timeout=DEADLINE)
except subprocess.TimeoutExpired:
    # RED: trapped in the tight select()-EINVAL loop, will never return.
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except ProcessLookupError:
        pass
    proc.communicate()
    test_fail(
        f"--timeout={INT_MAX} trapped rsync in a tight loop: the copy did not "
        f"finish within {DEADLINE}s.  A near-INT_MAX timeout overflowed "
        "set_io_timeout()'s (io_timeout + 1) / 2 to a negative select_timeout "
        "(select() -> EINVAL -> 100% CPU spin).  Fix: compute allowed_lull "
        "overflow-safe (wider type) in set_io_timeout(), and cap a peer's "
        "MSG_IO_TIMEOUT in read_a_msg().")

# -- Oracle -------------------------------------------------------------------
if proc.returncode != 0:
    test_fail(f"rsync did not spin under --timeout={INT_MAX} but exited "
              f"non-zero (rc={proc.returncode}).  Output tail:\n"
              + '\n'.join(out.splitlines()[-20:]))
if not (dst / 'file').is_file():
    test_fail(f"rsync returned 0 under --timeout={INT_MAX} but did not copy the "
              f"file.  Output tail:\n" + '\n'.join(out.splitlines()[-20:]))

print("msg-io-timeout-overflow: -fwrapv rsync absorbed --timeout=INT_MAX without "
      "spinning; the copy completed normally.")

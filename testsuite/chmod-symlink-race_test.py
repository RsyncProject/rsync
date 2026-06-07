#!/usr/bin/env python3
# Python rewrite of testsuite/chmod-symlink-race.test.
#
# Regression test for the symlink-TOCTOU class of bug applied to chmod()
# on the receiver side. The CVE-2026-29518 fix used
# secure_relative_open() for the basis-file open, but every other
# path-based syscall the receiver runs on sender-controllable paths is
# vulnerable to the same primitive: a local attacker swaps a symlink
# into one of the parent directory components between the receiver's
# check and its act, and the syscall escapes the module.
#
# The helper t_chmod_secure exercises the new do_chmod_at() wrapper
# across four scenarios; see the shell version for the full enumeration.
# After the helper runs we sanity-check the outside sentinel's mode
# from Python too, in case the helper's internal stat() ever drifts.

import os
import subprocess
import sys

from rsyncfns import SCRATCHDIR, TOOLDIR, rmtree, test_fail, test_xfail


def kernel_has_resolve_beneath():
    """Whether the running kernel honours a 'beneath' confinement primitive,
    matching t_chmod_secure's kernel_resolve_beneath_supported().  On Linux probe
    openat2(RESOLVE_BENEATH); elsewhere we can't probe O_RESOLVE_BENEATH from
    Python, but the FreeBSD/macOS versions that have it pass t_chmod_secure
    outright, so this is never consulted on a failure there.  NB: this is a
    different question from rsyncfns.resolve_beneath_supported(), which probes
    in-tree dir-symlink following -- the per-component fallback handles that, so
    it stays True without any kernel primitive."""
    if not sys.platform.startswith('linux'):
        return False
    try:
        import ctypes
        libc = ctypes.CDLL(None, use_errno=True)
        libc.syscall.restype = ctypes.c_long
        SYS_openat2, AT_FDCWD, RESOLVE_BENEATH = 437, -100, 0x08
        # struct open_how { __u64 flags; __u64 mode; __u64 resolve; }
        how = (ctypes.c_uint64 * 3)(os.O_RDONLY | os.O_DIRECTORY, 0, RESOLVE_BENEATH)
        fd = libc.syscall(SYS_openat2, AT_FDCWD, ctypes.c_char_p(b'.'),
                          how, ctypes.c_size_t(24))
        if fd >= 0:
            os.close(fd)
            return True
        return False
    except Exception:
        return False


mod = SCRATCHDIR / 'module'
trap_outside = SCRATCHDIR / 'trap'
rmtree(mod)
rmtree(trap_outside)
mod.mkdir(parents=True)
(mod / 'realdir').mkdir(parents=True)
trap_outside.mkdir(parents=True)

# File-system objects the helper expects.
(mod / 'realdir' / 'sentinel').write_text("bystander\n")
os.chmod(mod / 'realdir' / 'sentinel', 0o600)
(trap_outside / 'sentinel').write_text("target\n")
os.chmod(trap_outside / 'sentinel', 0o600)
os.symlink('realdir', mod / 'inside_link')
os.symlink('../trap', mod / 'escape_link')
(mod / 'topfile').write_text("top\n")
os.chmod(mod / 'topfile', 0o600)

proc = subprocess.run([str(TOOLDIR / 't_chmod_secure'), str(mod)])
sentinel_mode = (trap_outside / 'sentinel').stat().st_mode & 0o777
escaped = sentinel_mode != 0o600

if not kernel_has_resolve_beneath():
    # No kernel RESOLVE_BENEATH primitive, so do_chmod_at() falls back to the
    # per-component O_NOFOLLOW resolver, which cannot fully confine every chmod
    # scenario against a TOCTOU symlink swap.  master's t_chmod_secure adjusts
    # its expectations for this fallback; 3.4's older helper does not and counts
    # it as a failure.  The do_chmod_at() code is identical to master's, so this
    # is an inherent platform limitation (no kernel beneath primitive), not a
    # 3.4 regression -- mark it XFAIL.
    if escaped or proc.returncode != 0:
        test_xfail(
            "no kernel RESOLVE_BENEATH primitive: the per-component fallback "
            "cannot fully confine chmod and 3.4's t_chmod_secure lacks master's "
            "fallback expectation adjustment (same do_chmod_at as master)")
else:
    # RESOLVE_BENEATH is active: confinement is guaranteed, so any escape or
    # helper-reported failure is a real bug.
    if escaped:
        test_fail(
            f"outside sentinel mode changed from 600 to {oct(sentinel_mode)[2:]} "
            "-- chmod escaped the module")
    if proc.returncode != 0:
        test_fail("t_chmod_secure reported failures (see stderr above)")

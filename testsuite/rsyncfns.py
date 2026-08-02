"""Shared helpers for rsync's Python test scripts.

This is the Python counterpart of testsuite/rsync.fns. It exposes only what
the Python-rewritten tests actually need; grow it as more shell tests are
ported.

Conventions matching the shell harness:
  * Exit codes (see the Exit enum): 0=pass, 1=fail, 2=error, 77=skip, 78=xfail.
  * The runner sets these environment variables before invoking each test:
      scratchdir   per-test scratch directory
      srcdir       rsync source directory
      TOOLDIR      build directory (holds the rsync binary and helpers)
      RSYNC        the rsync command line (may include valgrind / --protocol=N)
      TLS_ARGS     extra arguments to pass to the 'tls' helper
      suitedir     this directory (testsuite/)
"""

from __future__ import annotations

import atexit
import fcntl
import filecmp
import errno
import math
import os
import platform
import re
import shlex
import shutil
import signal
import socket as _socket
import stat
import struct
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from exitcodes import Exit   # re-exported: tests may `from rsyncfns import Exit`


# --- environment -----------------------------------------------------------

def _required(name: str) -> str:
    v = os.environ.get(name)
    if not v:
        sys.stderr.write(
            f"rsyncfns: required environment variable {name} is not set; "
            "run this test via runtests.py rather than directly.\n"
        )
        sys.exit(Exit.ERROR)
    return v


SCRATCHDIR = Path(_required('scratchdir'))
SRCDIR = Path(_required('srcdir'))
TOOLDIR = Path(_required('TOOLDIR'))
SUITEDIR = Path(os.environ.get('suitedir', SRCDIR / 'testsuite'))

# rsync.fns set `umask 022` for every shell test, so the suite's expected
# file/dir modes are computed against that baseline. Mirror it here so the
# Python tests are deterministic regardless of the caller's ambient umask
# (e.g. a CI runner with umask 077) -- several permission tests depend on
# newly-created dirs being 0755. Individual tests may still narrow it (e.g.
# chmod-option uses 002 for its --chmod comparison).
os.umask(0o022)

# rsync.fns overrides HOME to $scratchdir; tests that exercise ssh-style
# transfers with no path component (e.g. localhost: at end of args) rely on
# HOME pointing at the per-test scratch dir.
os.environ['HOME'] = str(SCRATCHDIR)
RSYNC = _required('RSYNC')         # full command line, possibly with valgrind/protocol

# The "peer" rsync command -- used for the SERVER side of two-sided transfers
# (the daemon process; the remote-shell --rsync-path target). The runner sets
# RSYNC_PEER to a second binary when invoked with --rsync-bin2, letting a run
# mix two rsync versions over the wire. When no second binary was selected,
# RSYNC_PEER == RSYNC, so every consumer below behaves exactly as before and
# single-binary runs are unchanged. Use .get (not _required) so a test invoked
# by hand without the runner still works.
RSYNC_PEER = os.environ.get('RSYNC_PEER', RSYNC)


def split_rsync_cmd(cmd: str) -> list:
    """Split an rsync command string into argv, tolerating spaces in the path.

    RSYNC may be a wrapper command ('valgrind --tool=memcheck /build/rsync'),
    which has to be split, or a plain path to the binary, which must not be if
    it contains a space -- shlex.split() would turn '/ws test/rsync' into two
    nonexistent programs.  A path that exists is one word by definition, so
    check that first and only fall back to splitting for a real command line.

    Call this at use time, never once at import: tests such as chown-fake
    append ' --fake-super' to rsyncfns.RSYNC part-way through, and a cached
    split would keep handing back the pre-mutation command.
    """
    if os.path.isfile(cmd):
        return [cmd]
    # The path may be followed by options -- chown-fake and friends append
    # ' --fake-super' to RSYNC -- so the whole string is no longer a filename.
    # Take the longest leading run that names an existing file as the program
    # and split only what follows.
    for m in reversed(list(re.finditer(r'\s+', cmd))):
        head = cmd[:m.start()]
        if os.path.isfile(head):
            return [head] + shlex.split(cmd[m.start():])
    return shlex.split(cmd)


def _under_valgrind():
    """True when the runner wrapped rsync in valgrind (runtests.py --valgrind).

    Match the wrapper's program name (first token of RSYNC or RSYNC_PEER), not a
    bare 'valgrind' substring, so an rsync path that merely contains the word
    does not false-trigger.
    """
    for cmd in (RSYNC, RSYNC_PEER):
        if os.path.basename(shlex.split(cmd)[0]) == 'valgrind':
            return True
    return False

# TLS_ARGS controls how the 'tls' helper formats listings (e.g. --atimes,
# -l, -L). Tests that exercise non-default rsync features (atimes, etc.)
# assign to rsyncfns.TLS_ARGS before calling checkit / rsync_ls_lR.
TLS_ARGS = os.environ.get('TLS_ARGS', '')

# Daemon-mode transport. The DEFAULT is the secure stdio-pipe mechanism
# (RSYNC_CONNECT_PROG), which opens no listening socket at all. The runner
# sets RSYNC_TEST_USE_TCP=1 only when invoked with --use-tcp, which switches
# daemon tests to a real rsyncd bound to loopback (see start_test_daemon).
USE_TCP = os.environ.get('RSYNC_TEST_USE_TCP') == '1'

# Budget (seconds) a TOCTOU symlink-race test may spend trying to win its race
# before giving up. Set by runtests.py --race-timeout; when the operator did not
# pass it, each test keeps its own default (see race_budget).
_RACE_TIMEOUT_SET = 'race_timeout' in os.environ
try:
    RACE_TIMEOUT = float(os.environ.get('race_timeout', '5'))
except ValueError:
    # A malformed value counts as NOT set, so every test keeps its own default.
    # Falling back to the 5s baseline while still counting as "set" would
    # silently cut a 10s or 15s oracle in half -- weakening a security test
    # that nobody asked to shorten, which is the exact failure this knob's
    # validation exists to prevent.
    RACE_TIMEOUT = 5.0
    _RACE_TIMEOUT_SET = False


def race_budget(default: float = 5.0) -> float:
    """Seconds this race test may spend trying to provoke an escape.

    A race test is a NEGATIVE oracle: it passes by *failing* to break in before
    the budget runs out, so it always spends the whole budget. The budget is
    therefore the test's runtime -- these are the suite's slowest tests, which
    is exactly why the knob has to work.

    Tests needing longer than the 5s baseline to be a credible oracle pass their
    own `default`. An explicit --race-timeout overrides every default, in both
    directions: the old `max(RACE_TIMEOUT, 10.0)` idiom ignored the option below
    10s, so the documented knob did nothing for most of these tests.

    Lowering the budget weakens the oracle (fewer flips observed = less chance
    of catching a regression), so the defaults here are deliberately generous.

    A non-positive or non-finite budget is never a legitimate request: the race
    loop would run zero times and the test would report PASS without testing
    anything. runtests.py rejects that at the command line; ignoring it here too
    keeps the guarantee when race_timeout arrives straight from the environment.
    """
    if _RACE_TIMEOUT_SET and math.isfinite(RACE_TIMEOUT) and RACE_TIMEOUT > 0:
        return RACE_TIMEOUT
    return default

# Mnemonics for rsync's itemize-changes (-i / -ii) format:
#   all_plus   ->  +++++++++   every attribute changed (an additive create)
#   allspace   ->             every attribute unchanged
#   dots       ->  .....       trailing dots after the change columns
all_plus = '+++++++++'
allspace = '         '
dots = '.....'

# The "$tmpdir/from", "$tmpdir/to", "$tmpdir/chk" layout from rsync.fns.
TMPDIR = SCRATCHDIR
FROMDIR = SCRATCHDIR / 'from'
TODIR = SCRATCHDIR / 'to'
CHKDIR = SCRATCHDIR / 'chk'
CHKFILE = SCRATCHDIR / 'rsync.chk'
OUTFILE = SCRATCHDIR / 'rsync.out'


# --- result reporting ------------------------------------------------------

def test_fail(msg: str) -> 'None':
    sys.stderr.write(msg.rstrip() + '\n')
    sys.exit(Exit.FAIL)


def test_skipped(msg: str) -> 'None':
    sys.stderr.write(msg.rstrip() + '\n')
    (TMPDIR / 'whyskipped').write_text(msg.rstrip() + '\n')
    sys.exit(Exit.SKIP)


def test_xfail(msg: str) -> 'None':
    sys.stderr.write(msg.rstrip() + '\n')
    sys.exit(Exit.XFAIL)


# --- rsync invocation ------------------------------------------------------

# --- TCP port coordination across parallel tests ---------------------------

_PORT_LOCK_PATH = '/tmp/rsync_test.lck'
_port_lock_fd = None
_reaped_stale = False

# The lock file doubles as a registry of the rsyncd pid bound to each port, so a
# later run that wins the (orphan-released) lock can find and reap a daemon a
# SIGKILLed run stranded. The byte-range LOCKS sit at offsets 0..65535 (one byte
# per port number); the pid RECORDS sit in a separate region past them, one
# native-endian int32 (a pid_t) per port, written/read only while holding that
# port's lock so they're never raced. The file is host-local, so native endian is
# fine; an all-zero record (a sparse/older lock file) reads back as pid 0.
_PORT_PID_BASE = 1 << 16      # past every possible port lock byte (port < 65536)
_PORT_PID_REC = 8             # two native-endian int32 per port: (pgid, pid)

# Bytes 0..3 hold a magic identifying the lock-file layout. A fresh (all-zero)
# file gets it written under the byte-0 lock; a non-zero value that doesn't match
# means a stale file from an incompatible testsuite layout -- we error rather
# than misread the (pgid, pid) records. Bytes 0..3 also sit in the port-lock byte
# region, but ports 0..3 are never test ports so the overlap is harmless. Fixed
# arbitrary value; bump it on any on-disk layout change.
_LOCK_MAGIC = 0x9d4f2b8a


def _open_lock_file() -> int:
    """Open (or create) the host-wide port-lock file, defending against a
    local attacker who pre-plants the well-known /tmp path. CI runs some
    tests under sudo, so we must never let root open/chmod an attacker-
    controlled target.

    Strategy:
      * Try an O_EXCL|O_CREAT create. If we win, the file is brand-new,
        regular, owned by us and nlink==1 -- the ONLY case where we widen
        the mode to 0o666 (so a second user sharing the lock can open it
        RDWR; the create mode is otherwise narrowed by umask).
      * If it already exists, open it WITHOUT O_CREAT, WITHOUT chmod, and
        with O_NOFOLLOW so a planted symlink fails (ELOOP) rather than
        being followed. Then require a pristine regular file with nlink==1,
        rejecting a hard link to some other (e.g. root-owned 0600) file --
        O_NOFOLLOW alone does not catch hard links.
    """
    nofollow = getattr(os, 'O_NOFOLLOW', 0)

    # Path 1: we create it ourselves, exclusively.
    try:
        fd = os.open(_PORT_LOCK_PATH,
                     os.O_CREAT | os.O_EXCL | os.O_RDWR | nofollow, 0o666)
    except FileExistsError:
        fd = None
    if fd is not None:
        try:
            os.fchmod(fd, 0o666)  # we own this fresh file; undo umask
        except OSError:
            pass
        _check_or_write_magic(fd)
        return fd

    # Path 2: it already exists -- open without creating or chmod'ing.
    try:
        fd = os.open(_PORT_LOCK_PATH, os.O_RDWR | nofollow)
    except OSError as e:
        test_fail(f"cannot open lock file {_PORT_LOCK_PATH}: {e} "
                  "(refusing to follow a symlink -- possible tampering)")
    st = os.fstat(fd)
    if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
        os.close(fd)
        test_fail(f"lock file {_PORT_LOCK_PATH} is not a pristine regular "
                  f"file (type/nlink check failed -- possible tampering)")
    _check_or_write_magic(fd)
    return fd


def _check_or_write_magic(fd: int) -> 'None':
    """Validate (or stamp) the layout-version magic in bytes 0..3.

    Serialise on the byte-0 lock (also port 0's lock byte, never a test port) so
    two starting runs don't race the stamp. An all-zero header is a fresh file --
    write the magic. A non-zero header that doesn't match means a stale lock file
    from an incompatible testsuite layout (e.g. the old 4-byte pid records); error
    out so we never misread its records as (pgid, pid)."""
    fcntl.lockf(fd, fcntl.LOCK_EX, 4, 0)
    try:
        rec = os.pread(fd, 4, 0)
        cur = struct.unpack('=I', rec)[0] if len(rec) == 4 else 0
        if cur == 0:
            os.pwrite(fd, struct.pack('=I', _LOCK_MAGIC), 0)
        elif cur != _LOCK_MAGIC:
            os.close(fd)
            test_fail(f"lock file {_PORT_LOCK_PATH} has layout magic "
                      f"{cur:#010x}, expected {_LOCK_MAGIC:#010x} -- a stale file "
                      "from an incompatible testsuite. Remove it and retry.")
    except (OSError, struct.error):
        pass
    finally:
        try:
            fcntl.lockf(fd, fcntl.LOCK_UN, 4, 0)
        except OSError:
            pass


def _record_port_proc(port: int, pgid: int, pid: int) -> 'None':
    """Record (or clear, with pgid==pid==0) the test process group and rsyncd pid
    bound to `port`. The caller holds the port's lock. The pgid reaps the whole
    test (daemon + clients + flipper) with one killpg; the pid is the recycle
    guard (_pid_is_rsync) so we only kill a group still running our rsync."""
    if _port_lock_fd is None:
        return
    try:
        os.pwrite(_port_lock_fd, struct.pack('=ii', pgid, pid),
                  _PORT_PID_BASE + port * _PORT_PID_REC)
    except (OSError, struct.error):
        pass


def _read_port_proc(port: int) -> 'tuple':
    """Read the recorded (pgid, pid) for `port`, or (0, 0) if none. Caller holds
    the lock.

    Normalises a pid <= 1 to (0, 0): a record holding 0 / negative / garbage must
    NEVER be treated as a real pid (os.kill/os.killpg of 0 or -N would signal a
    whole process group). Only pid > 1 is a candidate, and _pid_is_rsync() still
    verifies it before any kill."""
    if _port_lock_fd is None:
        return (0, 0)
    try:
        rec = os.pread(_port_lock_fd, _PORT_PID_REC,
                       _PORT_PID_BASE + port * _PORT_PID_REC)
        if len(rec) != _PORT_PID_REC:
            return (0, 0)
        pgid, pid = struct.unpack('=ii', rec)
    except (OSError, struct.error):
        return (0, 0)
    return (pgid, pid) if pid > 1 else (0, 0)


def _pid_is_rsync(pid: int) -> bool:
    """True if `pid` is a live process whose command is rsync. Guards against a
    recycled pid before we kill it. Tries `ps -p N -o comm=` (precise, Linux/BSD/
    Solaris/macOS) and falls back to plain `ps -p N` (Cygwin's ps rejects -o but
    still prints the command). If neither confirms it, return False (leave the
    process alone)."""
    if pid <= 1:
        return False   # 0/-N would make os.kill signal a whole process group
    if pid == os.getpid():
        return False   # never signal ourselves
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    for argv in (['ps', '-p', str(pid), '-o', 'comm='], ['ps', '-p', str(pid)]):
        try:
            r = subprocess.run(argv, stdout=subprocess.PIPE,
                               stderr=subprocess.DEVNULL, text=True, timeout=5)
        except (OSError, subprocess.SubprocessError):
            return False
        if r.returncode == 0:          # ps understood the form -> answer is definitive
            return 'rsync' in r.stdout
    return False                       # no ps form worked -> don't kill


def _wait_pid_gone(pid: int, timeout: float) -> bool:
    """Poll up to `timeout` seconds for `pid` to stop being a live rsync.

    SIGKILL is asynchronous: the process may still be visible for a moment
    after it is signalled, so a single immediate check would call a dying
    process "still alive"."""
    deadline = time.monotonic() + timeout
    while True:
        if not _pid_is_rsync(pid):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.1)


def _reap_group(pgid: int, pid: int) -> bool:
    """Kill the test's whole process group (daemon + its clients + flipper) when
    `pid` is still a live rsync -- the portable recycle guard, so we only ever
    signal a group still running our rsync.  killpg(pgid) sweeps the group in one
    shot (the test driver runs in its own session, so the group is exactly that
    test's tree); if the pgid is unusable, fall back to killing the daemon pid
    alone.

    Returns True only when the daemon is CONFIRMED GONE, not merely when a
    signal was accepted.  The caller clears the port's registry entry on a True
    return, and that entry is the only handle anyone has on the process; on
    Cygwin a signal is routinely accepted by a process that then ignores it, so
    trusting the accept would discard the record while the port stays squatted
    -- leaving an occupant nothing can identify or reap.

    The confirmation is a bounded poll, not a single immediate check: SIGKILL is
    asynchronous, so a process still winding down is normal and answering
    "not gone" for it would make _probe_bindable() skip its retry and fail the
    test for a port that was about to free itself."""
    if not _pid_is_rsync(pid):
        return False
    try:
        if pgid > 1 and pgid != os.getpgrp():
            os.killpg(pgid, signal.SIGKILL)
        else:
            os.kill(pid, signal.SIGKILL)
    except OSError:
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
    if _wait_pid_gone(pid, 2.0):
        return True
    # Survived SIGKILL: on Cygwin that means it is stuck in a Windows call,
    # where only Windows can end it.
    _win_force_kill(pid)
    return _wait_pid_gone(pid, 2.0)


def _reap_orphan_daemon(port: int) -> bool:
    """Kill an orphaned test process group squatting `port`, if we can identify it.

    We hold the claim_ports() exclusive lock for `port`, so nothing we coordinate
    with owns it -- a still-bound port is an orphan a SIGKILLed run stranded (off
    Linux there's no PR_SET_PDEATHSIG backstop, so its --no-detach rsyncd outlives
    the test). start_rsyncd recorded that test's (pgid, pid); if the pid is still a
    live rsync, killpg the group. Returns True if it signalled something (caller
    re-probes the bind). Pure os/ps calls -> every platform."""
    pgid, pid = _read_port_proc(port)
    if not _reap_group(pgid, pid):
        return False
    _record_port_proc(port, 0, 0)
    time.sleep(0.2)   # let the kernel release the socket before the re-probe
    return True


def _reap_stale_daemons() -> 'None':
    """Intra-run sweep: kill every orphaned test rsyncd recorded in the lock file
    whose port-lock is free (no live test owns it), and clear its record.

    _reap_orphan_daemon() only fires when a NEW test claims the *same* port an
    orphan still squats; a daemon a SIGKILLed/timed-out test stranded on a port
    nothing else re-claims would otherwise linger for the whole run (off Linux
    there's no PR_SET_PDEATHSIG backstop), accumulating and exhausting ports until
    a later race test wedges.  This sweeps the whole pid registry so each test
    process reaps the leaks left by earlier ones.

    Run once per test process at the first claim_ports(), BEFORE this process has
    recorded any daemon of its own, so it never kills our own rsyncd.  A port a
    live concurrent test holds keeps its byte-lock, so LOCK_NB skips it; only a
    free-locked port with a recorded live rsync pid is a genuine orphan."""
    if _port_lock_fd is None:
        return
    try:
        size = os.fstat(_port_lock_fd).st_size
    except OSError:
        return
    if size <= _PORT_PID_BASE:
        return
    try:
        region = os.pread(_port_lock_fd, size - _PORT_PID_BASE, _PORT_PID_BASE)
    except OSError:
        return
    for port in range(min(len(region) // _PORT_PID_REC, 65536)):
        pgid, pid = struct.unpack('=ii', region[port*_PORT_PID_REC:(port+1)*_PORT_PID_REC])
        if pid <= 1:
            continue
        # Grab the port's byte-lock non-blocking: success => no live test owns it.
        try:
            fcntl.lockf(_port_lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB, 1, port)
        except OSError:
            continue   # a live test holds it -- not an orphan, leave it alone
        try:
            if _reap_group(pgid, pid):
                _record_port_proc(port, 0, 0)
        finally:
            try:
                fcntl.lockf(_port_lock_fd, fcntl.LOCK_UN, 1, port)
            except OSError:
                pass


def _probe_bindable(port: int, _reaped: bool = False, fatal: bool = True) -> bool:
    """Confirm `port` is actually free once we hold its claim_ports() lock.

    The byte-range lock only coordinates *live* test drivers, and the kernel
    releases it the instant the holding process dies -- even if that driver left
    an orphaned daemon still bound to the port. That happens when a run is
    SIGKILLed (or its ssh drops) on a platform with no parent-death backstop:
    rsyncfns only arms PR_SET_PDEATHSIG, which is Linux-only, so on the
    BSDs/Solaris/macOS a killed fleettest run can strand its rsyncd, which then
    squats the fixed test port. Because we recorded that rsyncd's pid in the lock
    file (and hold the lock now, proving it's not a live run), we can reap it and
    retry rather than failing -- see _reap_orphan_daemon.

    So actually try to bind it. SO_REUSEADDR is used so a port merely in
    TIME_WAIT (recently and cleanly closed) is NOT a false positive; only a
    live bound/listening socket -- a real squatter -- makes the bind fail. The
    probe socket is closed immediately, freeing the port for the daemon that is
    about to bind it.
    """
    s = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
    s.setsockopt(_socket.SOL_SOCKET, _socket.SO_REUSEADDR, 1)
    try:
        s.bind(('127.0.0.1', port))
        return True
    except OSError as e:
        err = e
    finally:
        s.close()
    # Bound by a squatter. If it's our own stranded orphan, kill it and retry once.
    if not _reaped and _reap_orphan_daemon(port):
        return _probe_bindable(port, _reaped=True, fatal=fatal)
    if not fatal:
        return False
    test_fail(
        f"port {port} was claimed for this run but something is still bound "
        f"to 127.0.0.1:{port} ({err.strerror}). The claim_ports() lock only "
        "serializes live test runs, so a still-bound port almost always "
        "means an orphaned 'rsync --daemon' from a previously killed run "
        f"(find it with `fstat | grep {port}` / `netstat -an | grep {port}` "
        "and kill it, or run `fleettest.py --cleanup`), then retry.")
    return False


def claim_ports(*ports: int) -> 'None':
    """Reserve the given TCP port numbers for the rest of this process.

    Uses POSIX byte-range locks on /tmp/rsync_test.lck (one byte per port,
    offset = port number) so that any number of tests can run in parallel
    without colliding on a port: if another test has already claimed any of
    the requested ports the call blocks until that test exits. The kernel
    drops POSIX advisory locks automatically when the holding process
    terminates, so a crashed test releases its ports without manual
    cleanup.

    Ports are claimed in sorted order, so two callers that ask for the same
    set in different orders can't deadlock against each other.

    Call once near the top of any test that binds to a specific TCP port,
    BEFORE the bind:

        from rsyncfns import claim_ports
        claim_ports(12873)
        listener = socket.socket(...)
        listener.bind(('127.0.0.1', 12873))

    The lock file lives in /tmp so it's shared across all rsync test
    processes on the host. Ports outside the claim_ports() ecosystem are
    not protected -- nothing stops an unrelated process from binding the
    port. For the rsync testsuite that's fine; we just need to avoid
    collisions between concurrent test scripts.
    """
    global _port_lock_fd, _reaped_stale
    if _port_lock_fd is None:
        _port_lock_fd = _open_lock_file()
    if not _reaped_stale:
        # Intra-run cleanup: reap any daemon an earlier test in this run stranded,
        # BEFORE we record one of our own.  Once per process is enough.
        _reaped_stale = True
        _reap_stale_daemons()
    for port in sorted(ports):
        # F_SETLKW via fcntl.lockf(LOCK_EX, length, start): exclusive
        # byte-range lock on byte `port`, blocking until acquired.
        fcntl.lockf(_port_lock_fd, fcntl.LOCK_EX, 1, port)
        # The lock only proves no other live test run owns the port; an orphaned
        # daemon from a killed run can still squat it (see _probe_bindable).
        _probe_bindable(port)


def claim_free_port(preferred: int) -> int:
    """Claim `preferred`, or a nearby port if something else is squatting it.

    claim_ports() fails loudly on an occupied port, which is right for a test
    that binds the port itself: it must not silently drift away from the number
    it is about to bind.  start_test_daemon() owns both the bind and the URL it
    hands back, so it can simply move instead -- and needs to, because a fixed
    test port can be permanently held by unrelated software on a shared CI box
    (a vendor service was found sitting on 13010 on the Windows/Cygwin target),
    which no amount of orphan reaping will free.

    Returns the port actually claimed.
    """
    global _port_lock_fd, _reaped_stale
    if _port_lock_fd is None:
        _port_lock_fd = _open_lock_file()
    if not _reaped_stale:
        _reaped_stale = True
        _reap_stale_daemons()
    candidates = [preferred] + [preferred + off for off in (1000, 2000, 3000, 4000)]
    for port in candidates:
        if not 1024 < port < 65536:
            continue
        fcntl.lockf(_port_lock_fd, fcntl.LOCK_EX, 1, port)
        if _probe_bindable(port, fatal=False):
            return port
    test_fail(
        f"no usable TCP port near {preferred}: every candidate "
        f"({', '.join(str(p) for p in candidates)}) is bound by something "
        "outside the testsuite.  Check with `netstat -an` and free one, or "
        "run `fleettest.py --cleanup` if they are stranded test daemons.")
    return preferred


# --- standalone rsyncd helpers ---------------------------------------------

def _set_pdeathsig() -> 'None':
    """Linux: ask the kernel to send SIGTERM to us if our parent dies.
    A no-op on every other platform. Used as preexec_fn so a kill -9 of
    the test process doesn't strand the rsyncd we spawned.

    The daemon deliberately stays in the TEST's process group: runtests.py
    killpg's that group when a test times out, and that is what keeps a
    timed-out test from stranding its daemon. Giving the daemon a group of
    its own would put it out of reach of that sweep; its per-connection
    children are handled by _daemon_children() instead."""
    if not sys.platform.startswith('linux'):
        return
    try:
        import ctypes
        libc = ctypes.CDLL('libc.so.6', use_errno=True)
        PR_SET_PDEATHSIG = 1
        libc.prctl(PR_SET_PDEATHSIG, 15, 0, 0, 0)  # 15 == SIGTERM
    except OSError:
        pass


def _win_force_kill(pid: int) -> 'None':
    """Cygwin last resort: terminate `pid` through Windows.

    Cygwin signals are cooperative -- delivered via a helper thread in the
    target -- so a process sitting in a Windows call ignores even SIGKILL.
    Such a process keeps its listening socket and cannot be reaped by any
    amount of kill/killpg/pkill. Map the cygwin pid to its Windows pid (the
    4th column of `ps -W`) and let Windows do it. A no-op elsewhere."""
    if not sys.platform.startswith('cygwin'):
        return
    try:
        r = subprocess.run(['ps', '-W'], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return
    for line in r.stdout.splitlines():
        f = line.split()
        if len(f) >= 4 and f[0] == str(pid):
            try:
                subprocess.run(['taskkill', '/F', '/PID', f[3]],
                               capture_output=True, timeout=10)
            except (OSError, subprocess.SubprocessError):
                pass
            return


def _daemon_children(pid: int) -> list:
    """PIDs whose parent is `pid` -- the connection handlers rsyncd forked.

    Cygwin's ps understands neither -A nor -o, but `ps -W` prints
    PID PPID PGID WINPID as its first four columns; everywhere else POSIX
    `ps -A -o pid=,ppid=` does the job. Returns [] if neither works: the
    caller then just kills the parent, which is the old behaviour."""
    argv = (['ps', '-W'] if sys.platform.startswith('cygwin')
            else ['ps', '-A', '-o', 'pid=,ppid='])
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return []
    kids = []
    for line in r.stdout.splitlines():
        f = line.split()
        if len(f) >= 2 and f[1] == str(pid):
            try:
                kids.append(int(f[0]))
            except ValueError:
                pass
    return kids


def _kill_pid(pid: int) -> 'None':
    """SIGTERM, then SIGKILL, then -- on Cygwin -- Windows.

    Gated on _pid_is_rsync at every step: these pids were snapshotted before the
    parent was killed, so between the snapshot and the signal a child can exit
    and the kernel can hand its pid to something else. Re-checking that the pid
    is STILL a live rsync before each signal keeps a recycled pid from getting
    the harness to kill an unrelated process.

    KNOWN LIMITATION: check-then-signal is inherently a TOCTOU, so the window is
    narrowed to microseconds rather than closed -- the pid would have to be
    recycled onto ANOTHER rsync in that gap to matter. Closing it properly needs
    a stable process reference (pidfd, or a retained Windows handle), which is
    per-platform machinery this suite has to run on seven of; the same guard is
    what _reap_group() has always used."""
    for sig in (signal.SIGTERM, signal.SIGKILL):
        if not _pid_is_rsync(pid):
            return          # gone, or the pid now belongs to someone else
        try:
            os.kill(pid, sig)
        except OSError:
            return          # already gone
        time.sleep(0.3)
    if _pid_is_rsync(pid):
        _win_force_kill(pid)


def _stop_rsyncd(proc) -> 'None':
    """Stop the test daemon AND the connection handlers it forked.

    Killing proc alone -- the only pid the Popen handle knows -- is what left
    the orphan: rsyncd forks a child per connection, and one still winding up
    or down when the test ends outlives the parent, inherits the listening
    socket and squats the port. Snapshot the children BEFORE killing the
    parent, because once it is gone they are reparented to init and no longer
    identifiable as ours.

    KNOWN LIMITATION: that is also why nothing is collected when the parent has
    ALREADY exited on its own (it crashed, say) while a child still holds the
    port -- by then the parent-child link this relies on is gone. The runner's
    per-test-timeout killpg covers the common case, since the daemon stays in
    the test's process group; `fleettest.py --cleanup` sweeps the rest."""
    if proc.poll() is not None:
        return
    kids = _daemon_children(proc.pid)
    try:
        proc.terminate()
        proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
            proc.wait(timeout=1)
        except (subprocess.TimeoutExpired, OSError):
            pass
        _win_force_kill(proc.pid)
    except OSError:
        pass
    for kid in kids:
        _kill_pid(kid)


def _cleanup_rsyncd(proc, port: int) -> 'None':
    """atexit handler: stop the daemon and clear its pid slot. A clean exit thus
    leaves no orphan to reap; only a SIGKILL (which skips atexit) leaves the slot
    set -- exactly the case _reap_orphan_daemon() needs it for.

    The slot is kept only while it still NAMES something reapable -- i.e. our
    daemon pid is somehow still a live rsync, which on Cygwin means it ignored
    the kill. That is the only case where the record buys a later run anything.

    Keying this on the port being busy instead looks safer but is worse: a port
    sits in TIME_WAIT after a passing test, so a plain bind fails and a record
    naming an already-dead pid is retained forever. Nothing ever clears such a
    record, and if that pid is later recycled onto an unrelated rsync the stale
    sweep would signal it. A record naming a dead process cannot help anyone --
    every reaper rejects it at the _pid_is_rsync guard -- so clearing it is
    strictly better.

    KNOWN LIMITATION: if the daemon parent died on its own while a connection
    child still held the port, that child is unreachable either way -- the
    record only ever named the parent. `fleettest.py --cleanup` sweeps it."""
    _stop_rsyncd(proc)
    if not _pid_is_rsync(proc.pid):
        _record_port_proc(port, 0, 0)


def start_rsyncd(conf_path, port: int, rsync_cmd: str = None) -> 'subprocess.Popen':
    """Spawn `rsync --daemon --no-detach --address=127.0.0.1 --port=N
    --config=conf` and return the Popen handle after the port is accepting
    connections.

    The daemon is bound to LOOPBACK ONLY (--address=127.0.0.1): without it,
    rsync --daemon binds 0.0.0.0 and the test modules would be reachable from
    the whole LAN. The daemon is killed automatically when this Python
    process exits (atexit). On Linux, the kernel also signals SIGTERM to the
    daemon if the parent dies abruptly (PR_SET_PDEATHSIG), so a SIGKILL on
    the test process doesn't strand the daemon either. The caller is expected
    to have already claim_ports()'d `port`.

    rsync_cmd selects the binary to run as the daemon; it defaults to
    RSYNC_PEER (the peer side of a two-sided run), so ordinary daemon tests
    get current-client <-> peer-daemon. The reverse-direction test passes
    rsync_cmd=RSYNC to put the current build on the daemon side and drive with
    the old client.

    This is only ever reached from start_test_daemon() in --use-tcp mode; the
    default (pipe) mode never starts a listening daemon.
    """
    argv = shlex.split(rsync_cmd or RSYNC_PEER) + [
        '--daemon', '--no-detach',
        '--address=127.0.0.1',
        f'--port={port}',
        f'--config={conf_path}',
    ]
    proc = subprocess.Popen(
        argv,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        preexec_fn=_set_pdeathsig,
    )
    # Record this test's process group (os.getpgrp() -- the daemon and its clients
    # and flipper all live in the per-test session runtests.py started) together
    # with this --no-detach rsyncd's pid, while we still hold the port's lock, so a
    # later test/run can killpg the whole stranded tree (see _reap_orphan_daemon).
    _record_port_proc(port, os.getpgrp(), proc.pid)
    atexit.register(_cleanup_rsyncd, proc, port)

    deadline = time.monotonic() + 10
    last_err = None
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            test_fail(
                f"rsyncd exited before listening on port {port} "
                f"(status={proc.returncode})"
            )
        try:
            with _socket.create_connection(('127.0.0.1', port), timeout=0.5):
                return proc
        except OSError as e:
            last_err = e
            time.sleep(0.05)

    _stop_rsyncd(proc)
    test_fail(f"rsyncd never listened on 127.0.0.1:{port}: {last_err}")


def start_test_daemon(conf_path, port: int, rsync_cmd: str = None) -> str:
    """Bring up the test daemon and return a URL prefix for client commands.

    rsync_cmd selects the daemon-side binary (default RSYNC_PEER); pass
    rsync_cmd=RSYNC for the reverse-direction test (current daemon, old client).

    This is the single seam every daemon test uses. The transport depends on
    the mode the runner selected:

      * DEFAULT (secure) -- no TCP socket at all. Sets RSYNC_CONNECT_PROG so
        the rsync client forks the daemon over a private stdio pipe. Returns
        'rsync://localhost/'. Another local user can't reach it; nothing is
        listening.

      * --use-tcp -- starts a real rsyncd bound to 127.0.0.1 on the given
        claim_ports()-reserved port. Returns 'rsync://localhost:PORT/'. Bound
        to loopback so off-host/LAN access is impossible; a same-host user
        could still connect during the test window, which is the documented,
        accepted cost of explicitly opting into TCP.

    Build URLs as f"{prefix}module/path". `port` is only used (and claimed)
    in --use-tcp mode.
    """
    daemon_cmd = rsync_cmd or RSYNC_PEER
    if USE_TCP:
        port = claim_free_port(port)
        start_rsyncd(conf_path, port, daemon_cmd)
        return f'rsync://localhost:{port}/'
    # RSYNC_CONNECT_PROG is run by a shell, so every word has to survive
    # re-parsing: a build path with a space would otherwise exec its prefix.
    os.environ['RSYNC_CONNECT_PROG'] = (
        f'{rsync_path_arg(daemon_cmd)} --config={shlex.quote(str(conf_path))} --daemon')
    return 'rsync://localhost/'


def require_tcp(reason: str) -> 'None':
    """Skip the test (exit 77) unless we're in --use-tcp mode. For tests that
    fundamentally need a real listening socket / TCP peer and have no secure
    pipe equivalent (the fake-proxy listener; the reverse-DNS hostname-ACL
    daemon test)."""
    if not USE_TCP:
        test_skipped(reason)


def require_asan(reason: str, which: str = None) -> 'None':
    """Skip the test (exit 77) unless the rsync binary is AddressSanitizer-
    instrumented. `which` defaults to the daemon/peer command (RSYNC_PEER);
    pass RSYNC to check the client side. Detection runs the binary with
    ASAN_OPTIONS=help=1, which makes an instrumented binary print the ASan
    flag help banner to stderr."""
    cmd = split_rsync_cmd(which or RSYNC_PEER)
    try:
        r = subprocess.run(cmd + ['--version'],
                           env={**os.environ, 'ASAN_OPTIONS': 'help=1'},
                           stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                           timeout=15)
    except Exception:
        test_skipped(reason)
        return
    if b'AddressSanitizer' not in r.stderr:
        test_skipped(reason)


def rsh_cmd(cmd: str = None, *opts: str) -> str:
    """Build an RSYNC_RSH / --rsh value, quoted for rsync's own tokenizer.

    rsync splits this string on spaces itself -- honouring ' and ", see do_cmd()
    in main.c -- so a remote-shell path containing a space must be quoted or
    rsync execs only the first word.  The testsuite's srcdir can contain one.
    """
    if cmd is None:
        cmd = str(SRCDIR / 'support' / 'lsh.sh')
    return ' '.join([shlex.quote(cmd), *opts])


def rsync_path_arg(cmd: str = None) -> str:
    """Value for --rsync-path, quoted for the shell that will re-parse it.

    --rsync-path is a command line run by the remote shell, not a filename, so
    rsync hands it over unquoted and the far side word-splits it.  A build path
    containing a space therefore needs quoting here, while a wrapper command
    ('valgrind ... /build/rsync') must stay several words.  Splitting and
    re-joining with shlex gives both: each word is quoted only if it needs it.
    """
    return shlex.join(split_rsync_cmd(RSYNC_PEER if cmd is None else cmd))


def rsync_argv(*args: str) -> list:
    """Return the argv for invoking rsync with the given extra arguments.

    RSYNC may be a multi-word command (e.g. 'valgrind ... /build/rsync'); we
    shlex-split it so subprocess sees a proper argv list. Each *args entry
    is appended verbatim, so callers should pass tokens already split (no
    embedded option/value joined by spaces).
    """
    return split_rsync_cmd(RSYNC) + list(args)


import functools as _functools


@_functools.lru_cache(maxsize=64)
def rsync_supports(flag: str) -> bool:
    """Does the configured rsync binary accept ``flag``?

    Probes by invoking ``rsync <flag> --version`` and checking the exit code +
    stderr.  C rsync accepts every flag we'd care about and exits 0 before
    --version prints; other implementations (gokrazy/rsync, openrsync) reject
    unsupported flags with "unknown option" / "unrecognized option" /
    "no such option" and a non-zero exit.

    Used by tests that want to *optionally* pass a hardening flag like
    `--no-inc-recursive` (only meaningful where the implementation has
    incremental recursion to disable).  When the probe is inconclusive (e.g.
    timeout) the helper returns True so tests fall back to today's C-rsync
    behaviour.
    """
    try:
        r = subprocess.run(rsync_argv(flag, '--version'),
                           capture_output=True, text=True, timeout=5)
    except (subprocess.TimeoutExpired, OSError):
        return True
    if r.returncode == 0:
        return True
    stderr = (r.stderr or '').lower()
    for marker in ('unknown option', 'unrecognized option', 'no such option'):
        if marker in stderr:
            return False
    # Non-zero exit but no recognizable "unknown" marker -- assume supported.
    return True


def forced_protocol():
    """The protocol version pinned via --protocol=N in the RSYNC command, or
    None when the run isn't pinning one (so the binary negotiates its newest).
    Protocol-sensitive tests use this to gate sub-cases -- e.g. the split
    between --append and --append-verify only exists at protocol >= 30; at
    protocol 29 plain --append behaves like the old verifying append."""
    import re
    m = re.search(r'--protocol[ =](\d+)', RSYNC)
    return int(m.group(1)) if m else None


def run_rsync(*args: str, check: bool = True,
              capture_output: bool = False) -> subprocess.CompletedProcess:
    """Run rsync with the given arguments.

    By default, stdout/stderr inherit (so the runner captures them in the
    per-test log). Set capture_output=True if the test needs to inspect the
    output. If check is True (the default), a non-zero exit calls
    test_fail() with the rsync command line.
    """
    argv = rsync_argv(*args)
    if capture_output:
        proc = subprocess.run(argv, capture_output=True, text=True)
    else:
        proc = subprocess.run(argv)
    if check and proc.returncode != 0:
        test_fail(f"rsync exited {proc.returncode}: {' '.join(argv)}")
    return proc


# --- filesystem helpers ----------------------------------------------------

def makepath(*paths) -> 'None':
    """Equivalent of rsync.fns makepath: mkdir -p, but for multiple paths."""
    for p in paths:
        os.makedirs(p, exist_ok=True)


def rmtree(path) -> 'None':
    """Remove a tree if it exists, ignoring missing entries."""
    p = Path(path)
    if p.exists() or p.is_symlink():
        shutil.rmtree(p, ignore_errors=True)


def is_a_link(path) -> bool:
    """True if 'path' is a symbolic link (dangling or not)."""
    return os.path.islink(path)


def start_path_flipper(name_a, name_b):
    """Spawn a separate PROCESS that repeatedly swaps two sibling paths
    name_a <-> name_b in a tight rename loop, for TOCTOU symlink-race tests:
    point one at a real directory and the other at a symlink so the shared name
    keeps flipping between a directory and a symlink under a running rsync.

    A separate process (not a thread) is used deliberately: a Python thread
    contends with the test's own loop for the GIL and flips far too slowly to
    win the race.  The swap is three renames via a scratch name in the same
    directory, so the shared name is absent only for the brief instant between
    two renames (rsync just gets ENOENT and retries).

    The caller should stop it with stop_flipper(), but the flipper also
    self-terminates: it exits when its parent (the test process) goes away --
    os.getppid() changes once the test is reaped -- and after a hard deadline as a
    backstop.  Without this, a test killed before its stop_flipper() finally (a
    timeout, a crash) would leak an orphan that keeps renaming paths in the shared
    scratch and poisons later tests on the same box.  os.getppid() is POSIX, so
    this is portable across the fleet.

    Returns a subprocess.Popen; the caller must stop it with stop_flipper()."""
    code = (
        "import os, sys, time\n"
        "a, b = sys.argv[1], sys.argv[2]\n"
        "tmp = a + '.flip'\n"
        "parent = os.getppid()\n"
        "deadline = time.monotonic() + 300\n"
        "while os.getppid() == parent and time.monotonic() < deadline:\n"
        "    try:\n"
        "        os.rename(a, tmp); os.rename(b, a); os.rename(tmp, b)\n"
        "    except OSError:\n"
        "        pass\n"
    )
    return subprocess.Popen([sys.executable, '-c', code, str(name_a), str(name_b)])


def stop_flipper(proc):
    """Stop a start_path_flipper()/start_c_flipper() process."""
    proc.terminate()
    try:
        proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


# A compiled flipper wins TOCTOU races far more reliably than the Python one:
# on a journaled disk fs it does ~2x the swaps/sec with a plain rename loop and
# ~7x with renameat2(RENAME_EXCHANGE) -- which is also denser, as EXCHANGE is a
# single atomic syscall with no transient missing-name window.  Compiled on
# demand against the build's config.h so the renameat2/portability guards match
# the target; falls back to the Python flipper where no compiler is available.
_C_FLIPPER_SRC = r'''
/* testsuite flipper: repeatedly swap two sibling names a<->b so a shared path
 * keeps flipping (typically real-dir <-> symlink) under a running rsync.
 * Prefers atomic renameat2(RENAME_EXCHANGE); falls back to a 3-rename dance.
 * Self-terminates when its parent (the test) goes away, plus a deadline
 * backstop, so a killed test never leaks an orphan that poisons later tests.
 * Built on demand by rsyncfns.compile_c_flipper(); not linked into rsync. */
#define _GNU_SOURCE 1
#include "config.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <fcntl.h>
#include <errno.h>
#include <time.h>
#include <sys/stat.h>
#if defined(__linux__)
# include <sys/syscall.h>
# ifndef RENAME_EXCHANGE
#  define RENAME_EXCHANGE (1 << 1)
# endif
#endif

static double mono(void) {
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return t.tv_sec + t.tv_nsec / 1e9;
}

static int use_exchange = 1;

static void flip(const char *a, const char *b) {
#if defined(__linux__) && defined(SYS_renameat2)
    if (use_exchange) {
        if (syscall(SYS_renameat2, AT_FDCWD, a, AT_FDCWD, b, RENAME_EXCHANGE) == 0)
            return;
        if (errno == ENOSYS || errno == EINVAL || errno == EOPNOTSUPP)
            use_exchange = 0;   /* kernel or filesystem lacks EXCHANGE */
        else
            return;             /* transient race error (e.g. ENOENT): retry */
    }
#endif
    {
        char tmp[4096];
        if (snprintf(tmp, sizeof tmp, "%s.flip", a) >= (int)sizeof tmp)
            return;                 /* too long: don't act on a truncated name */
        rmdir(tmp); unlink(tmp);    /* clear a stale scratch from a wedged half-swap */
        if (rename(a, tmp) != 0) {
            mkdir(a, 0700);         /* a was consumed: recreate so the next loop swaps */
            return;
        }
        if (rename(b, a) != 0)
            rename(tmp, a);         /* b gone: restore a, retry next loop */
        else
            rename(tmp, b);         /* complete the swap */
    }
}

int main(int argc, char **argv) {
    if (argc < 3) {
        fprintf(stderr, "usage: %s PATH_A PATH_B\n", argv[0]);
        return 2;
    }
    const char *a = argv[1], *b = argv[2];
    pid_t parent = getppid();
    double deadline = mono() + 300.0;   /* backstop if never reaped */
    while (getppid() == parent && mono() < deadline)
        flip(a, b);
    return 0;
}
'''

_c_flipper_bin = None  # None=untried, ''=unavailable, else path


def _detect_cc():
    """The C compiler the tree was built with (so the flipper matches the
    target), falling back to a plain cc/gcc/clang on PATH."""
    cc = os.environ.get('CC')
    if cc:
        return cc
    import re
    # The generated Makefile lives in the BUILD dir (TOOLDIR); for a VPATH build
    # that is not SRCDIR.  Prefer TOOLDIR, fall back to SRCDIR for an in-tree build.
    for d in (TOOLDIR, SRCDIR):
        mk = d / 'Makefile'
        if mk.is_file():
            m = re.search(r'(?m)^CC\s*=\s*(.+?)\s*$', mk.read_text())
            if m and m.group(1):
                return m.group(1)
    for c in ('cc', 'gcc', 'clang'):
        if shutil.which(c):
            return c
    return None


def compile_c_flipper():
    """Build (once, cached) the C flipper against the build's config.h.  Returns
    its path, or None if no compiler is available (caller uses the Python flipper)."""
    global _c_flipper_bin
    if _c_flipper_bin is not None:
        return _c_flipper_bin or None
    cc = _detect_cc()
    src = SCRATCHDIR / 't_flipper.c'
    out = SCRATCHDIR / ('t_flipper' + ('.exe' if os.name == 'nt' else ''))
    if not cc:
        _c_flipper_bin = ''
        return None
    src.write_text(_C_FLIPPER_SRC)
    # config.h is generated into the BUILD dir (TOOLDIR); the Makefile compiles
    # with `-I. -I$(srcdir)`, so mirror that (TOOLDIR first, then SRCDIR).
    cmd = (shlex.split(cc)
           + ['-O2', f'-I{TOOLDIR}', f'-I{SRCDIR}', '-o', str(out), str(src)])
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          text=True)
    if proc.returncode != 0 or not os.access(out, os.X_OK):
        _c_flipper_bin = ''
        return None
    _c_flipper_bin = str(out)
    return _c_flipper_bin


def start_c_flipper(name_a, name_b):
    """Like start_path_flipper() but execs the compiled flipper for a far higher
    flip rate (a stronger RED oracle on slow filesystems).  Transparently falls
    back to the Python flipper where no compiler is available.  Returns a Popen;
    stop with stop_flipper()."""
    binpath = compile_c_flipper()
    if binpath:
        return subprocess.Popen([binpath, str(name_a), str(name_b)])
    return start_path_flipper(name_a, name_b)


def cp_p(src, dst) -> 'None':
    """Equivalent of rsync.fns cp_p: copy preserving mode + timestamps."""
    shutil.copy2(src, dst)


def make_data_file(path, size: int) -> 'None':
    """Equivalent of rsync.fns make_data_file: create `path` with `size`
    bytes of non-trivial content suitable for rsync's delta algorithm.

    Prefers /dev/urandom for speed. Falls back to a deterministic LCG
    seeded from PID and the destination path so successive calls produce
    distinct content -- matching the shell helper.
    """
    path = str(path)
    if os.path.exists('/dev/urandom'):
        try:
            with open('/dev/urandom', 'rb') as src, open(path, 'wb') as dst:
                remaining = size
                while remaining:
                    chunk = src.read(min(remaining, 1 << 16))
                    if not chunk:
                        break
                    dst.write(chunk)
                    remaining -= len(chunk)
            if remaining == 0:
                return
        except OSError:
            pass

    # Fallback: BSD-LCG to printable-ASCII (33..126), so output stays
    # exactly `size` bytes regardless of awk/utf8 quirks the shell
    # version worked around.
    path_seed = int.from_bytes(path.encode(), 'big') & 0xFFFFFFFF
    state = (os.getpid() + path_seed) % 2147483648
    with open(path, 'wb') as f:
        out = bytearray(size)
        for i in range(size):
            state = (state * 1103515245 + 12345) % 2147483648
            out[i] = ((state >> 16) % 94) + 33
        f.write(bytes(out))


def make_text_file(path, lines: int = 100) -> 'None':
    """Write a predictable, self-contained text file of `lines` lines.

    This replaces the old habit of capturing `ls -l /etc` / `ls -l /bin`
    (falling back to `ls /`) into the test tree. Those tied the fixtures
    to the host filesystem layout: the directories are absent or
    unreadable on Android/Termux and other minimal environments, where
    `ls /` fails outright, and the captured content was never
    reproducible. The output here is deterministic and depends on nothing
    outside the suite, so every platform builds the identical fixture.
    """
    content = ''.join(
        "line %06d  the quick brown fox jumps over the lazy dog  %d %d\n"
        % (i, (i * 31) % 97, (i * 131) % 89)
        for i in range(1, lines + 1)
    )
    with open(str(path), 'w') as f:
        f.write(content)


def get_testuid() -> int:
    return os.getuid()


def get_rootuid() -> int:
    return 0


def get_rootgid() -> int:
    return 0


def build_rsyncd_conf() -> 'Path':
    """Equivalent of rsync.fns build_rsyncd_conf.

    Writes $scratchdir/test-rsyncd.conf with the four standard modules
    (test-from, test-to, test-scratch, test-hidden) and a $scratchdir/
    ignore23 wrapper that propagates rsync's exit status except for
    code 23 (vanished/missing source files), which it eats so that the
    surrounding test can tolerate the partial-transfer case.

    Returns the path to the config file. Tests typically follow up by
    setting RSYNC_CONNECT_PROG so rsync forks an in-tree daemon instead
    of contacting one over the network.
    """
    conf = SCRATCHDIR / 'test-rsyncd.conf'
    pidfile = SCRATCHDIR / 'rsyncd.pid'
    logfile = SCRATCHDIR / 'rsyncd.log'

    my_uid = get_testuid()
    root_uid = get_rootuid()
    root_gid = get_rootgid()

    if my_uid != root_uid:
        # Non-root cannot specify uid/gid in rsyncd.conf.
        uid_line = f"#uid = {root_uid}"
        gid_line = f"#gid = {root_gid}"
    else:
        uid_line = f"uid = {root_uid}"
        gid_line = f"gid = {root_gid}"

    conf.write_text(f"""\
# rsyncd configuration file autogenerated by rsyncfns.build_rsyncd_conf

pid file = {pidfile}
use chroot = no
munge symlinks = no
# Loopback only. In --use-tcp mode the daemon is also bound to 127.0.0.1
# (start_rsyncd passes --address), so this is belt-and-suspenders; in the
# default pipe mode there is no socket to guard at all.
hosts allow = localhost 127.0.0.0/8
log file = {logfile}
transfer logging = yes
# We don't define log format here so the test-hidden module defaults
# to the internal static string (since we had a crash trying to tweak it).
exclude = ? foobar.baz
max verbosity = 4
{uid_line}
{gid_line}

[test-from]
\tpath = {FROMDIR}
\tlog format = %i %h [%a] %m (%u) %l %f%L
\tread only = yes
\tcomment = r/o

[test-to]
\tpath = {TODIR}
\tlog format = %i %h [%a] %m (%u) %l %f%L
\tread only = no
\tcomment = r/w

[test-scratch]
\tpath = {SCRATCHDIR}
\tlog format = %i %h [%a] %m (%u) %l %f%L
\tread only = no

[test-hidden]
\tpath = {FROMDIR}
\tlist = no
""")

    ignore23 = SCRATCHDIR / 'ignore23'
    ignore23.write_text(
        '#!/bin/sh\n'
        'if "${@}"; then exit; fi\n'
        'ret=$?\n'
        'if test $ret = 23; then exit; fi\n'
        'exit $ret\n'
    )
    ignore23.chmod(0o755)

    return conf


def rsync_getgroups() -> list:
    """List of group ids the test user is a member of, via the getgroups
    test helper binary. Mirrors rsync.fns rsync_getgroups."""
    out = subprocess.check_output([str(TOOLDIR / 'getgroups')], text=True)
    return out.split()


# --- extended attributes (per-OS surface) ----------------------------------
# Mirrors the per-OS xset/xls/RSYNC_PREFIX/RUSR logic from the old
# testsuite/rsync.fns + xattrs.test so the xattr / fake-super tests run on
# Linux, macOS and FreeBSD (not just Linux). Test attributes use literal
# names ("user.foo" etc., exactly as the shell did on every platform); only
# rsync's own fake-super attribute name (RSYNC_PREFIX, used for the
# "%stat" attr) and the special "equal" attr (RUSR) vary by OS.

_SYSTEM = platform.system()

# Cygwin reports "CYGWIN_NT-10.0-..." and uses Linux-style user.* xattrs
# (rsync builds there with HAVE_LINUX_XATTRS), but CPython on Cygwin lacks
# os.*xattr, so we drive the getfattr/setfattr CLIs there instead.
_CYGWIN = _SYSTEM.startswith('CYGWIN')

# Platforms whose user xattrs live in the "user." namespace encoded in the
# attribute name (Linux and Cygwin). macOS/FreeBSD carry the namespace out
# of band and a literal "user." prefix is actually rejected there.
_LINUX_NS = _SYSTEM == 'Linux' or _CYGWIN

# Test attribute names are LOGICAL (un-prefixed, e.g. "foo", "rsync.%stat");
# _xattr_full() adds the "user." prefix on the Linux-namespace platforms.
# RSYNC_PREFIX is the logical name of rsync's own fake-super attr ("rsync"
# -> "rsync.%stat", and "user.rsync.%stat" on Linux/Cygwin). RUSR is the
# prefix for the test's "equal" attr; macOS and Solaris use "rsync.nonuser"
# to stay clear of rsync's reserved "rsync.*" space.
RSYNC_PREFIX = 'rsync'
RUSR = 'rsync.nonuser' if _SYSTEM in ('Darwin', 'SunOS') else 'rsync'


def _xattr_full(name: str) -> str:
    """Map a logical user-xattr name to the on-disk name for this OS."""
    return ('user.' + name) if _LINUX_NS else name


def xattrs_supported() -> bool:
    """True if this rsync was built with xattr support AND this platform has
    a way for the tests to set/list user xattrs."""
    vv = run_rsync('-VV', check=True, capture_output=True).stdout
    if '"xattrs": true' not in vv:
        return False
    if _SYSTEM == 'Linux':
        return hasattr(os, 'setxattr')
    if _CYGWIN:
        return shutil.which('setfattr') is not None
    if _SYSTEM == 'Darwin':
        return shutil.which('xattr') is not None
    if _SYSTEM == 'FreeBSD':
        return shutil.which('setextattr') is not None
    if _SYSTEM == 'SunOS':
        return shutil.which('runat') is not None
    return False  # NetBSD/etc.: not yet ported


class XattrError(OSError):
    """An xattr CLI refused an operation.

    Deliberately an OSError: on Linux xattr_set() calls os.setxattr(), where a
    refusal IS an OSError, so callers write `except OSError`.  Every other
    platform shells out, and a bare CalledProcessError -- not an OSError --
    sails straight past that handler.  A refusal the suite tolerates on Linux
    would then be fatal everywhere else.

    .errno is set only when the tool named one.  Most of these tools report a
    message rather than a number, and guessing an errno from localised
    strerror text would be worse than admitting we do not know, so a caller
    that switches on .errno must cope with None.
    """


def _tool_errno(tool: str, msg: str) -> 'int | None':
    """The errno a tool named for itself, or None if it named none.

    Only macOS's xattr(1) reports one, as "xattr: [Errno 13] Permission
    denied: '/some/path'".  Match that tool's own prefix, and only on the
    first line: the rest of the line is a filename, and a file can perfectly
    well be called "[Errno 5]" -- searching the whole message would let the
    file being operated on dictate the errno we report.
    """
    if tool != 'xattr' or not msg:
        return None
    import re
    m = re.match(r'xattr: \[Errno (\d+)\]', msg.splitlines()[0])
    return int(m.group(1)) if m else None


def _xattr_run(argv, **kwargs) -> 'None':
    """Run an xattr CLI, raising XattrError rather than CalledProcessError."""
    proc = subprocess.run(argv, capture_output=True, text=True, **kwargs)
    if proc.returncode == 0:
        return
    msg = (proc.stderr or proc.stdout or '').strip()
    err = XattrError(f'{argv[0]} exited {proc.returncode}'
                     + (f': {msg}' if msg else ''))
    err.errno = _tool_errno(argv[0], msg)
    raise err


def xattr_set(name: str, value: str, *paths) -> 'None':
    """Set the user-namespace xattr `name` (logical) = `value` on each path.

    Raises OSError (see XattrError) on every platform if the set is refused."""
    full = _xattr_full(name)
    for p in paths:
        p = str(p)
        if _SYSTEM == 'Linux':
            os.setxattr(p, full.encode(), value.encode())
        elif _CYGWIN:
            _xattr_run(['setfattr', '-n', full, '-v', value, p])
        elif _SYSTEM == 'Darwin':
            _xattr_run(['xattr', '-w', full, value, p])
        elif _SYSTEM == 'FreeBSD':
            _xattr_run(['setextattr', '-h', 'user', full, value, p])
        elif _SYSTEM == 'SunOS':
            # Solaris extended attributes are a per-file namespace; runat
            # cd's into it and runs a shell that reads the script on stdin
            # (the -c form mangles args). Pass name/value via the environment
            # to dodge quoting; printf writes the value with no trailing
            # newline, matching the byte-exact value other platforms store.
            _xattr_run(
                ['runat', p, '/bin/sh'],
                input='printf %s "$XVAL" > "$XNAME"\n',
                env={**os.environ, 'XNAME': full, 'XVAL': value})
        else:
            raise NotImplementedError(f"xattr_set on {_SYSTEM}")


def xattr_dump(*paths) -> str:
    """Return a deterministic name=value dump of the user xattrs on `paths`,
    for comparing a source tree against its rsync'd copy. The format only
    needs to be self-consistent on a given OS (we never compare across OSes),
    mirroring the per-OS xls() in the old xattrs.test."""
    if _SYSTEM == 'Linux':
        # Read xattrs natively (symmetric with the os.setxattr used in
        # xattr_set) so the suite needs no external getfattr. The attr
        # package's CLI tools are frequently absent -- on Android/Termux
        # and minimal CI images -- even when the filesystem itself supports
        # user xattrs, in which case shelling out to getfattr would crash
        # the test instead of exercising it. The output mimics "getfattr
        # -d": a "# file:" header then sorted name="value" lines, files
        # with no user xattrs omitted.
        out = []
        for p in paths:
            sp = str(p)
            names = sorted(n for n in os.listxattr(sp) if n.startswith('user.'))
            if not names:
                continue
            out.append(f'# file: {sp}\n')
            for n in names:
                v = os.getxattr(sp, n).decode('utf-8', 'surrogateescape')
                out.append(f'{n}="{v}"\n')
            out.append('\n')
        return ''.join(out)
    if _CYGWIN:
        # Python on Cygwin lacks os.*xattr, so use the CLI there.
        return subprocess.check_output(
            ['getfattr', '-d', *(str(p) for p in paths)], text=True)
    if _SYSTEM == 'Darwin':
        out = []
        for p in paths:
            t = subprocess.check_output(['xattr', '-l', str(p)], text=True)
            out.append('\n'.join(ln.lstrip(' \t') for ln in t.splitlines()))
            out.append('\n')
        return ''.join(out)
    if _SYSTEM == 'FreeBSD':
        out = []
        for p in paths:
            names = subprocess.check_output(
                ['lsextattr', '-q', '-h', 'user', str(p)], text=True).split()
            for n in sorted(names):
                out.append(subprocess.check_output(
                    ['getextattr', '-h', 'user', n, str(p)], text=True))
        return ''.join(out)
    if _SYSTEM == 'SunOS':
        # List the file's extended-attribute namespace via runat (script on
        # stdin), skipping the always-present SUNWattr_* system attrs, and
        # dump name=value (sorted glob order; $(cat) drops a trailing newline).
        script = ('for x in *; do case "$x" in SUNWattr_*) continue;; esac; '
                  'printf "%s=%s\\n" "$x" "$(cat "$x")"; done\n')
        out = []
        for p in paths:
            out.append(subprocess.run(
                ['runat', str(p), '/bin/sh'], input=script,
                capture_output=True, text=True, check=True).stdout)
        return ''.join(out)
    raise NotImplementedError(f"xattr_dump on {_SYSTEM}")


def runtest(label: str, fn, *args, **kwargs):
    """Run a sub-test step with an echoed label, like rsync.fns runtest.

    The shell helper does `Test $1: $2 ... done.` -- this prints a similar
    banner and propagates exceptions (which surface as a failing test).
    """
    print(f"Test {label}: ", end="", flush=True)
    fn(*args, **kwargs)
    print("done.")


def cp_touch(src, dst) -> 'None':
    """Equivalent of rsync.fns cp_touch: copy preserving timestamps, then
    forcibly re-touch both source and destination to identical times.

    On some filesystems cp rounds microsecond timestamps on the destination;
    rsync.fns works around this by then `touch -r dst src dst`. Here we set
    both src and dst to dst's mtime/atime after the copy, so a diff of the
    tls output (which prints times) sees identical entries on both sides.
    """
    shutil.copy2(src, dst)
    if os.path.isdir(dst):
        dst = os.path.join(dst, os.path.basename(src))
    st = os.stat(dst, follow_symlinks=False)
    os.utime(src, ns=(st.st_atime_ns, st.st_mtime_ns), follow_symlinks=False)
    os.utime(dst, ns=(st.st_atime_ns, st.st_mtime_ns), follow_symlinks=False)


def build_symlinks() -> 'None':
    """Equivalent of rsync.fns build_symlinks: a set of canonical relative,
    absolute, dangling and unsafe symlinks under FROMDIR for symlink tests.
    """
    FROMDIR.mkdir(parents=True, exist_ok=True)
    (FROMDIR / 'referent').write_text(
        subprocess.check_output(['date'], text=True)
    )
    os.symlink('referent', FROMDIR / 'relative')
    os.symlink(str(FROMDIR / 'referent'), FROMDIR / 'absolute')
    os.symlink('nonexistent', FROMDIR / 'dangling')
    os.symlink(str(SRCDIR / 'rsync.c'), FROMDIR / 'unsafe')


def hands_setup() -> 'None':
    """Populate FROMDIR with a varied tree of files and directories for the
    canonical 'hands' transfer test.

    All content is generated from within the suite (srcdir contents plus
    make_text_file output) so the fixture is self-contained and
    reproducible on every platform.
    """
    rmtree(FROMDIR)
    rmtree(TODIR)
    TMPDIR.mkdir(parents=True, exist_ok=True)
    FROMDIR.mkdir(parents=True, exist_ok=True)
    TODIR.mkdir(parents=True, exist_ok=True)

    (FROMDIR / 'empty').touch()
    (FROMDIR / 'emptydir').mkdir(exist_ok=True)

    # File list of srcdir contents, generated through the tls helper so it
    # matches the format the rest of the suite uses.
    (FROMDIR / 'filelist').write_text(rsync_ls_lR(SRCDIR))

    # The shell test uses `echo -n` semantics; write_text without a trailing
    # newline is the cleanest equivalent.
    (FROMDIR / 'nolf').write_text("This file has no trailing lf")

    old_umask = os.umask(0)
    try:
        os.symlink('nolf', FROMDIR / 'nolf-symlink')
    finally:
        os.umask(old_umask)

    # Concatenate all *.c files in srcdir into a single 'text' file.
    text = bytearray()
    for c in sorted(SRCDIR.glob('*.c')):
        text.extend(c.read_bytes())
    (FROMDIR / 'text').write_bytes(bytes(text))

    (FROMDIR / 'dir').mkdir(exist_ok=True)
    shutil.copy(FROMDIR / 'text', FROMDIR / 'dir')
    (FROMDIR / 'dir' / 'subdir').mkdir(exist_ok=True)
    (FROMDIR / 'dir' / 'subdir' / 'foobar.baz').write_text("some data\n")
    (FROMDIR / 'dir' / 'subdir' / 'subsubdir').mkdir(exist_ok=True)
    # Predictable, self-contained fixture files (the names etc-ltr-list /
    # bin-lt-list are kept because other tests reference them by name).
    make_text_file(FROMDIR / 'dir' / 'subdir' / 'subsubdir' / 'etc-ltr-list', 120)

    (FROMDIR / 'dir' / 'subdir' / 'subsubdir2').mkdir(exist_ok=True)
    make_text_file(FROMDIR / 'dir' / 'subdir' / 'subsubdir2' / 'bin-lt-list', 200)


# --- listing / verification ------------------------------------------------

def rsync_ls_lR(directory) -> str:
    """Equivalent of rsync.fns rsync_ls_lR: print a sorted ls-style listing
    of `directory`, pruning .git / auto-build-save / testtmp subtrees, using
    the project's `tls` helper so the output format matches the rest of the
    suite.
    """
    cmd = (
        "find . -name .git -prune -o -name auto-build-save -prune "
        "-o -name testtmp -prune -o -print | sort | sed 's/ /\\\\ /g' | "
        f"xargs '{TOOLDIR}/tls' {TLS_ARGS}"
    )
    # tls can emit bytes that are not valid UTF-8 (a filename or symlink target
    # with high bytes); decode with backslashreplace so Python 3.14's strict
    # UTF-8 text mode doesn't raise UnicodeDecodeError.  Unlike surrogateescape
    # this yields a clean str (bad bytes shown as \xNN) that callers can safely
    # write_text()/print() without re-raising, and it is deterministic so the
    # listing still compares consistently.
    proc = subprocess.run(['sh', '-c', cmd], capture_output=True,
                          encoding='utf-8', errors='backslashreplace',
                          cwd=str(directory))
    return proc.stdout


def checkit(args, expected_dir, actual_dir, skip_file_diff: bool = False,
            allowed_codes=(0,)) -> 'None':
    """Run rsync with `args` (a list of extra rsync arguments) and then
    verify two things:

      1. The tls-formatted listings of `expected_dir` and `actual_dir`
         are identical.
      2. (Unless skip_file_diff) diff -r against the two trees reports
         no differences.

    `allowed_codes` is the tuple of exit codes treated as success.
    Pass (0, 23) for daemon-mode transfers that may report partial-
    transfer codes even when the listings still match.

    Calls test_fail() on any mismatch. Mirrors the rsync.fns checkit shell
    helper; callers pass rsync arguments as a Python list rather than as a
    pre-quoted command string, which avoids the shell-quoting gymnastics
    that the shell version needed.
    """
    expected_dir = str(expected_dir)
    actual_dir = str(actual_dir)

    failed = []

    # If TLS_ARGS asks for atimes, the listing must be captured BEFORE the
    # rsync run because diff'ing files afterwards updates their atimes.
    ls_from = None
    if '--atimes' in TLS_ARGS:
        ls_from = rsync_ls_lR(expected_dir)

    print(f"Running: rsync {' '.join(args)}")
    proc = subprocess.run(rsync_argv(*args))
    if proc.returncode not in allowed_codes:
        failed.append(f"status={proc.returncode}")

    if ls_from is None:
        ls_from = rsync_ls_lR(expected_dir)
    ls_to = rsync_ls_lR(actual_dir)

    print("-------------")
    print("check how the directory listings compare with diff:")
    print()
    if ls_from != ls_to:
        ls_from_path = TMPDIR / 'ls-from'
        ls_to_path = TMPDIR / 'ls-to'
        ls_from_path.write_text(ls_from)
        ls_to_path.write_text(ls_to)
        diff = subprocess.run(
            ['diff', '-u', str(ls_from_path), str(ls_to_path)],
            capture_output=True, text=True,
        )
        sys.stdout.write(diff.stdout)
        failed.append("dir-diff")

    print("-------------")
    print("check how the files compare with diff:")
    print()
    if skip_file_diff:
        print("  === Skipping (as directed) ===")
    else:
        diff = subprocess.run(['diff', '-r', '-u', expected_dir, actual_dir])
        if diff.returncode != 0:
            failed.append("file-diff")

    print("-------------")
    if failed:
        test_fail("Failed: " + " ".join(failed))


def verify_dirs(expected_dir, actual_dir, skip_file_diff: bool = False,
                label: str = '') -> 'None':
    """Verify two directory trees match: identical tls listings and
    (unless skip_file_diff) identical file contents. Same comparison
    logic as checkit() but with no rsync invocation -- useful when the
    rsync that produced `actual_dir` had to be driven manually so that
    its output could be captured for inspection."""
    expected_dir = str(expected_dir)
    actual_dir = str(actual_dir)
    tag = f"{label}: " if label else ""

    ls_expected = rsync_ls_lR(expected_dir)
    ls_actual = rsync_ls_lR(actual_dir)
    if ls_expected != ls_actual:
        ls_expected_path = TMPDIR / 'ls-from'
        ls_actual_path = TMPDIR / 'ls-to'
        ls_expected_path.write_text(ls_expected)
        ls_actual_path.write_text(ls_actual)
        diff = subprocess.run(
            ['diff', '-u', str(ls_expected_path), str(ls_actual_path)],
            capture_output=True, text=True,
        )
        sys.stdout.write(diff.stdout)
        test_fail(f"{tag}directory listings differ between "
                  f"{expected_dir} and {actual_dir}")

    if not skip_file_diff:
        diff = subprocess.run(['diff', '-r', '-u', expected_dir, actual_dir])
        if diff.returncode != 0:
            test_fail(f"{tag}file content differs between "
                      f"{expected_dir} and {actual_dir}")


def v_filt(text: str) -> str:
    """Strip the boilerplate lines rsync emits at -v / -vv so callers can
    diff only the file/directory change lines. Mirrors rsync.fns v_filt:
    delete the build/progress banners, then everything from the first
    blank line to end-of-text."""
    out = []
    skip_prefix = (
        'building file list ',
        'sending incremental file list',
        'created directory ',
        'total: ',
        'client charset: ',
        'server charset: ',
    )
    for line in text.splitlines():
        if line == '':
            break
        if line.startswith(skip_prefix):
            continue
        if line == 'done':
            continue
        if line.endswith(' --whole-file'):
            continue
        out.append(line)
    return '\n'.join(out) + ('\n' if out else '')


def checkdiff(args, expected: str, *, filter=None, allowed_codes=(0,),
              direct: bool = False) -> 'None':
    """Run a command, capture its stdout, optionally pipe through `filter`,
    then compare to `expected`. Mirrors rsync.fns checkdiff/checkdiff2.

    args is normally a list of rsync arguments -- the rsync binary is
    prepended via rsync_argv. Pass direct=True to run `args` as a literal
    command (used by tests that drive a wrapper such as BATCH.sh).
    """
    if direct:
        argv = list(args)
        label = ' '.join(argv)
    else:
        argv = rsync_argv(*args)
        label = 'rsync ' + ' '.join(args)
    print(f"Running: {label}")
    proc = subprocess.run(argv, capture_output=True, text=True)
    stdout = proc.stdout
    if proc.stderr:
        sys.stderr.write(proc.stderr)
    sys.stdout.write(stdout)

    failed = []
    if proc.returncode not in allowed_codes:
        failed.append(f"status={proc.returncode}")

    if filter is not None:
        stdout = filter(stdout)

    if stdout != expected:
        from difflib import unified_diff
        diff = unified_diff(
            expected.splitlines(keepends=True),
            stdout.splitlines(keepends=True),
            fromfile='expected', tofile='got',
        )
        sys.stdout.write(''.join(diff))
        failed.append("output differs")

    if failed:
        test_fail("Failed: " + " ".join(failed))


def check_perms(path, expected: str) -> 'None':
    """Verify that the 9-char rwx permission string of `path` matches
    `expected` (e.g. 'rwx------'). Calls test_fail() on mismatch."""
    mode = os.stat(path, follow_symlinks=False).st_mode
    bits = [
        (0o400, 'r'), (0o200, 'w'), (0o100, 'x'),
        (0o040, 'r'), (0o020, 'w'), (0o010, 'x'),
        (0o004, 'r'), (0o002, 'w'), (0o001, 'x'),
    ]
    chars = [c if mode & bit else '-' for bit, c in bits]
    # Layer the setuid/setgid/sticky bits over x as the long-listing format does.
    if mode & 0o4000:
        chars[2] = 's' if mode & 0o100 else 'S'
    if mode & 0o2000:
        chars[5] = 's' if mode & 0o010 else 'S'
    if mode & 0o1000:
        chars[8] = 't' if mode & 0o001 else 'T'
    perms = ''.join(chars)
    if perms != expected:
        print(f"permissions: {perms} on {path}")
        print(f"should be:   {expected}")
        test_fail(f"check_perms failed for {path}")


# --- depth / cross-dir coverage helpers ------------------------------------
# Added for the option-coverage expansion (see testsuite/COVERAGE.md).
# The path-handling restructure changes how parent components resolve, so its
# bugs surface only at DEPTH and across directory boundaries -- these helpers
# build trees with an entry at every level and assert the concrete property an
# option controls (not just dest == src).

def make_tree(root, depth: int = 3, *, data: bool = False,
              content_lines: int = 20, data_size: int = 4096,
              dirname: str = 'd', leaf: str = 'f'):
    """Create a layered directory tree with one regular file at every level.

    For depth=3 under `root`:
        root/f0
        root/d1/f1
        root/d1/d2/f2
        root/d1/d2/d3/f3
    so an option's effect can be checked at the tree root AND >=3 levels deep
    (the parent-component resolution the path restructure rewrites).

    Returns (dirs, files): `dirs` the created subdirectories outermost-first,
    `files` the regular files shallow-first. Content is deterministic
    (make_text_file) unless data=True (make_data_file, delta-friendly).
    """
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    dirs = []
    files = []
    cur = root
    for level in range(depth + 1):
        f = cur / f'{leaf}{level}'
        if data:
            make_data_file(f, data_size)
        else:
            make_text_file(f, content_lines)
        files.append(f)
        if level < depth:
            cur = cur / f'{dirname}{level + 1}'
            cur.mkdir(exist_ok=True)
            dirs.append(cur)
    return dirs, files


def walk_files(root) -> list:
    """Every regular (non-symlink) file under `root`, sorted, recursively.
    For asserting a per-entry property holds at every depth."""
    root = Path(root)
    return sorted(p for p in root.rglob('*')
                  if p.is_file() and not p.is_symlink())


def walk_dirs(root) -> list:
    """Every subdirectory under `root`, sorted, recursively."""
    root = Path(root)
    return sorted(p for p in root.rglob('*')
                  if p.is_dir() and not p.is_symlink())


def _tag(label: str) -> str:
    return f"{label}: " if label else ""


def assert_same(a, b, label: str = '') -> 'None':
    """Fail unless files `a` and `b` have byte-identical content."""
    if not filecmp.cmp(str(a), str(b), shallow=False):
        test_fail(f"{_tag(label)}content differs between {a} and {b}")


def assert_mode(path, expected_octal: int, label: str = '') -> 'None':
    """Fail unless the permission bits (low 12) of `path` equal expected_octal
    (pass an int like 0o644). Does not follow symlinks."""
    mode = stat.S_IMODE(os.stat(path, follow_symlinks=False).st_mode)
    if mode != expected_octal:
        test_fail(f"{_tag(label)}mode {mode:04o} != expected "
                  f"{expected_octal:04o} on {path}")


def assert_mtime_close(a, b, tol: float = 1.0, label: str = '') -> 'None':
    """Fail unless the mtimes of `a` and `b` are within `tol` seconds.
    `b` may be a number (an explicit epoch mtime) instead of a path."""
    ma = os.stat(a, follow_symlinks=False).st_mtime
    mb = b if isinstance(b, (int, float)) else os.stat(
        b, follow_symlinks=False).st_mtime
    if abs(ma - mb) > tol:
        test_fail(f"{_tag(label)}mtime {ma} vs {mb} differ by > {tol}s "
                  f"(checking {a})")


def assert_is_symlink(path, target: str = None, label: str = '') -> 'None':
    """Fail unless `path` is a symlink (optionally pointing exactly at
    `target`)."""
    if not os.path.islink(path):
        test_fail(f"{_tag(label)}{path} is not a symlink")
    if target is not None:
        actual = os.readlink(path)
        if actual != target:
            test_fail(f"{_tag(label)}{path} -> {actual!r}, "
                      f"expected {target!r}")


def assert_hardlinked(a, b, label: str = '') -> 'None':
    """Fail unless `a` and `b` are the same inode (a hard link / --link-dest
    result)."""
    sa = os.stat(a, follow_symlinks=False)
    sb = os.stat(b, follow_symlinks=False)
    if (sa.st_dev, sa.st_ino) != (sb.st_dev, sb.st_ino):
        test_fail(f"{_tag(label)}{a} and {b} are not hard-linked "
                  f"(ino {sa.st_ino} vs {sb.st_ino})")


def assert_not_hardlinked(a, b, label: str = '') -> 'None':
    """Fail if `a` and `b` share an inode (e.g. --copy-dest must copy, not
    link)."""
    sa = os.stat(a, follow_symlinks=False)
    sb = os.stat(b, follow_symlinks=False)
    if (sa.st_dev, sa.st_ino) == (sb.st_dev, sb.st_ino):
        test_fail(f"{_tag(label)}{a} and {b} unexpectedly share "
                  f"inode {sa.st_ino}")


def assert_exists(path, label: str = '') -> 'None':
    """Fail unless `path` exists (a symlink counts even if dangling)."""
    if not os.path.lexists(path):
        test_fail(f"{_tag(label)}{path} does not exist")


def assert_not_exists(path, label: str = '') -> 'None':
    """Fail if `path` exists (a dangling symlink counts as existing)."""
    if os.path.lexists(path):
        test_fail(f"{_tag(label)}{path} exists but should not")


_psf_cache = None


def proc_self_fd_pins() -> bool:
    """True iff /proc/self/fd/N is a Linux-style magic symlink whose readlink
    yields the open file's real path -- the primitive rrsync's realpath-vs-exec
    inode-pin relies on.  macOS/BSD lack the directory; Solaris HAS /proc/self/fd
    but its entries are not such symlinks.  Mirrors rrsync's own HAVE_PROC_SELF_FD
    probe so the rrsync race test runs only where the protection actually exists
    (it falls through unpinned, by design, elsewhere).  Cached.

    A correct readlink does not prove the fd pins anything, and two platforms
    get this wrong in opposite directions: NetBSD makes the entry a symlink for
    directories only (readlink of a regular file's entry fails with EINVAL),
    while Cygwin's readlink is right but opening the magic link RE-RESOLVES the
    path, so a rename under a held fd reaches the replacement. Only Linux gives
    the inode-bound magic link, so require that and keep the probes as a guard
    for Linux-like environments without /proc."""
    global _psf_cache
    if _psf_cache is not None:
        return _psf_cache
    if not sys.platform.startswith(('linux', 'android')):
        _psf_cache = False
        return _psf_cache

    def resolves(path):
        try:
            fd = os.open(path, os.O_RDONLY)
        except OSError:
            return False
        try:
            return os.readlink('/proc/self/fd/%d' % fd) == os.path.realpath(path)
        except OSError:
            return False
        finally:
            os.close(fd)

    _psf_cache = resolves('/') and resolves(os.path.realpath(__file__))
    return _psf_cache


def write_daemon_conf(modules, globals=None, *,
                      name: str = 'test-rsyncd.conf') -> 'Path':
    """Write a custom rsyncd.conf for daemon-parameter tests.

    `modules` is a list of (module_name, {param: value}) pairs; `globals` an
    optional dict of global parameters that override the minimal defaults
    (pid file / use chroot=no / hosts allow / log file / max verbosity).
    Mirrors build_rsyncd_conf()'s root-aware uid/gid handling (only emitted
    when running as root) and writes the same `ignore23` wrapper, but lets a
    test set arbitrary parameters/modules beyond the fixed four. Returns the
    config path; pair with start_test_daemon().
    """
    conf = SCRATCHDIR / name
    pidfile = SCRATCHDIR / 'rsyncd.pid'
    logfile = SCRATCHDIR / 'rsyncd.log'

    g = {
        'pid file': str(pidfile),
        'use chroot': 'no',
        'hosts allow': 'localhost 127.0.0.0/8',
        'log file': str(logfile),
        'max verbosity': '4',
    }
    if globals:
        g.update(globals)
    if get_testuid() == get_rootuid():
        g.setdefault('uid', str(get_rootuid()))
        g.setdefault('gid', str(get_rootgid()))
    else:
        # Non-root cannot set uid/gid in rsyncd.conf.
        g.pop('uid', None)
        g.pop('gid', None)

    lines = ['# autogenerated by rsyncfns.write_daemon_conf', '']
    lines += [f'{k} = {v}' for k, v in g.items()]
    lines.append('')
    for mod_name, params in modules:
        lines.append(f'[{mod_name}]')
        lines += [f'\t{k} = {v}' for k, v in params.items()]
        lines.append('')
    conf.write_text('\n'.join(lines) + '\n')

    ignore23 = SCRATCHDIR / 'ignore23'
    if not ignore23.exists():
        ignore23.write_text(
            '#!/bin/sh\n'
            'if "${@}"; then exit; fi\n'
            'ret=$?\n'
            'if test $ret = 23; then exit; fi\n'
            'exit $ret\n'
        )
        ignore23.chmod(0o755)

    return conf


# --- security regression helpers -------------------------------------------

def expect_fail(argv, text, env=None, cwd=None):
    proc = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          text=True, env=env, cwd=cwd)
    out = (proc.stdout or '') + (proc.stderr or '')
    if proc.returncode == 0:
        test_fail(f"command unexpectedly succeeded: {argv!r}\n{out}")
    if text not in out:
        test_fail(f"expected {text!r} in command output:\n{out}")
    return proc


def patched_rrsync(workdir, rsync_path=None):
    # The stub rsync just has to exec successfully; the BSDs keep true(1) in
    # /usr/bin, not /bin, so resolve it on PATH rather than hard-coding /bin/true.
    import re
    if rsync_path is None:
        rsync_path = shutil.which('true') or '/usr/bin/true'
    src = SRCDIR / 'support' / 'rrsync'
    dst = Path(workdir) / 'rrsync-under-test'
    # Rewrite rrsync's hardcoded RSYNC path to our stub by matching the assignment
    # line, not its value: a distro/port (e.g. FreeBSD's net/rsync) may ship a
    # different path (/usr/local/bin/rsync), and a value-specific str.replace()
    # would silently no-op and leave rrsync exec'ing the real rsync -- a
    # server-mode hang.  A callable replacement avoids re backslash-escaping.
    text, n = re.subn(r"(?m)^RSYNC\s*=.*$",
                      lambda _m: f"RSYNC = {rsync_path!r}",
                      src.read_text())
    if n != 1:
        test_fail(f"patched_rrsync: expected exactly one 'RSYNC =' line in {src}, found {n}")
    dst.write_text(text)
    dst.chmod(0o755)
    return dst


def run_rrsync_denied(command, expected):
    base = SCRATCHDIR / expected.replace(' ', '_').replace('/', '_')
    base.mkdir(parents=True, exist_ok=True)
    restricted = base / 'restricted'
    restricted.mkdir(exist_ok=True)
    rrsync = patched_rrsync(base)
    env = {**os.environ, 'SSH_ORIGINAL_COMMAND': command}
    expect_fail([str(rrsync), '-ro', '-no-lock', str(restricted)], expected, env=env)


def make_proxy_server(port, response):
    listener = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
    listener.setsockopt(_socket.SOL_SOCKET, _socket.SO_REUSEADDR, 1)
    listener.bind(('127.0.0.1', port))
    listener.listen(1)

    def serve():
        conn, _ = listener.accept()
        try:
            conn.recv(65536)
            conn.sendall(response)
        finally:
            try:
                conn.close()
            finally:
                listener.close()

    import threading
    t = threading.Thread(target=serve)
    t.daemon = True
    t.start()
    return t


def run_proxy_probe(port, host, expected):
    env = {**os.environ, 'RSYNC_PROXY': f'127.0.0.1:{port}'}
    proc = subprocess.run(
        rsync_argv(f'rsync://{host}/mod/', str(SCRATCHDIR / 'proxy-out')),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
    out = (proc.stdout or '') + (proc.stderr or '')
    if proc.returncode == 0:
        test_fail(f"proxy probe unexpectedly succeeded:\n{out}")
    if expected not in out:
        test_fail(f"expected {expected!r} in proxy probe output:\n{out}")
    return proc


def setup_chroot_inner(name):
    if get_testuid() != get_rootuid():
        test_skipped("chroot /./ module regression requires root")
    if _under_valgrind():
        # The daemon's per-connection child chroots into the module, after
        # which valgrind can no longer create its absolute --log-file %p path
        # and the child dies (the transfer then resets) -- skip under valgrind.
        test_skipped("daemon chroot prevents valgrind from writing its per-process log")
    base = SCRATCHDIR / name
    outer = base / 'outer'
    inner = outer / 'inner'
    outside = outer / 'outside'
    src = base / 'src'
    rmtree(base)
    makepath(inner, outside, src)
    os.symlink('../outside', inner / 'linkparent')
    conf = write_daemon_conf([
        ('mod', {'path': str(outer) + '/./inner', 'read only': 'no',
                 'use chroot': 'yes', 'munge symlinks': 'no'}),
    ], name=f'{name}.conf')
    url = start_test_daemon(conf, 12940 + (abs(hash(name)) % 200))
    return base, inner, outside, src, url


def run_checked(argv):
    proc = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    return proc, (proc.stdout or '') + (proc.stderr or '')


def build_patched_rsync(name, replacements, append_cflags=None):
    # Cygwin can't reliably rebuild a single patched unit from the copied tree:
    # make leaves the copied object in place (coarse NTFS mtimes) so the patch is
    # silently absent, and forcing the rebuild trips gcc-13's -fno-common link
    # errors against the prebuilt objects.  The malicious-peer behaviour these
    # tests simulate is platform-independent and is exercised on every POSIX
    # target, so skip the unbuildable simulation here rather than misreport it.
    if sys.platform == 'cygwin' or platform.system().startswith('CYGWIN'):
        test_skipped(f"{name}: build_patched_rsync is unreliable on Cygwin "
                     "(prebuilt-object staleness / -fno-common relink); the "
                     "patched-peer fix is validated on the POSIX targets")
    if not (SRCDIR / 'Makefile').is_file():
        test_skipped(f"{name}: needs a configured rsync source tree with a Makefile")
    if not shutil.which('make'):
        test_skipped(f"{name}: make(1) not on PATH")
    if not shutil.which('gcc') and not shutil.which('cc'):
        test_skipped(f"{name}: no C compiler on PATH")

    work = SCRATCHDIR / name
    rmtree(work)
    shutil.copytree(
        SRCDIR, work, symlinks=True,
        ignore=shutil.ignore_patterns(
            'testtmp', '.git', 'auto-build-save', 'autom4te.cache', '__pycache__'))

    for relpath, old, new in replacements:
        path = work / relpath
        text = path.read_text()
        if old not in text:
            test_skipped(f"{name}: could not find patch target in {relpath}: {old!r}")
        path.write_text(text.replace(old, new, 1))
        # Drop the copied object for this unit.  copytree() preserves mtimes, so
        # on a target whose clock lags the host that pushed the tree -- or one
        # with coarse mtime granularity -- the prebuilt .o can look NEWER than
        # the just-patched .c, make reuses it, and the patch is silently absent.
        # The test then exercises an unmodified peer and reports a vacuous
        # result instead of a real one.
        obj = (work / relpath).with_suffix('.o')
        if obj.exists():
            obj.unlink()

    # Same hazard at the link step: a prebuilt binary with a future mtime looks
    # up to date even once its objects have been rebuilt.
    for stale in (work / 'rsync', work / 'rsync.exe'):
        if stale.exists():
            stale.unlink()

    # Optionally append compiler flags (e.g. -fwrapv) to the already-configured
    # CFLAGS line in the copied Makefile.  This preserves the platform's
    # configure-chosen flags and just adds to them, unlike a `make CFLAGS=...`
    # override which would drop them.
    if append_cflags:
        import re
        mkpath = work / 'Makefile'
        mk = mkpath.read_text()
        mk2 = re.sub(r'(?m)^(CFLAGS=.*)$', r'\1 ' + append_cflags, mk, count=1)
        if mk2 == mk:
            test_skipped(f"{name}: could not append {append_cflags!r} to CFLAGS in the Makefile")
        mkpath.write_text(mk2)
        # The copied tree carries prebuilt objects compiled with the ORIGINAL
        # flags; since the sources aren't newer, make would reuse them and the
        # appended flag would silently apply to nothing.  Drop them so every
        # unit recompiles with the new CFLAGS.
        for obj in work.rglob('*.o'):
            obj.unlink()

    env = {**os.environ, 'CCACHE_DISABLE': '1'}
    build = subprocess.run(['make', '-j2', 'rsync'], cwd=str(work), env=env,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    rsync = work / 'rsync'
    if build.returncode != 0 or not rsync.is_file() or not os.access(rsync, os.X_OK):
        test_skipped(
            f"{name}: patched rsync build failed (rc={build.returncode}). "
            "Tail of build output:\n" + '\n'.join(build.stdout.splitlines()[-20:]))
    return rsync


# --- operator-supplied-path symlink policy matrix --------------------------
#
# Policy: an operator-supplied path follows a symlink at any component iff that
# symlink is owned by uid 0 or the running euid, and refuses one owned by any
# other uid -- for absolute and relative paths alike.  --insecure-links is a
# LOCAL opt-out that restores legacy following (a daemon never honours it; that
# is covered by a separate daemon test).  run_symlink_matrix() drives one
# path-taking option through {cross-uid vs same-uid} x {absolute vs relative} x
# {symlink-at-leaf vs symlink-in-a-parent-component} x {--insecure-links off/on}
# and asserts each cell against the policy:  FOLLOW iff (insecure or same-uid).

def find_attacker_uid():
    """An untrusted uid (not 0, not the euid) for a cross-uid plant, else None."""
    import pwd
    for nm in ('nobody', 'nfsnobody', 'daemon'):
        try:
            u = pwd.getpwnam(nm).pw_uid
        except KeyError:
            continue
        if u != 0 and u != os.geteuid():
            return u
    return None


def run_symlink_matrix(option, case, *, paths=('abs', 'rel'),
                       wheres=('leaf', 'parent'), label=''):
    """Run `case(ctx)` over the operator-path symlink matrix; assert the policy.

    `case(ctx)` plants the option's path symlink (per ctx.where/ctx.abspath),
    runs rsync (honouring ctx.insecure), and returns True if the symlink was
    FOLLOWED (the op escaped to ctx.outside / read an out-of-tree object).
    ctx carries: base, outside, plant (fresh Paths); owner ('cross'|'self');
    att_uid; abspath ('abs'|'rel'); where ('leaf'|'parent'); insecure (bool);
    and plant_link(at, target) which symlinks target->at and lchowns it to the
    attacker uid when owner=='cross' (otherwise it stays euid-owned).

    Cross-uid cells need root (to own a symlink by a foreign uid) and are
    skipped otherwise; same-uid cells run at any uid.
    """
    import re
    import types
    tag = option + (f' [{label}]' if label else '')
    euid = os.geteuid()
    att = find_attacker_uid() if euid == 0 else None
    slug = re.sub(r'[^a-z0-9]+', '-', tag.lower()).strip('-')

    for abspath in paths:
        for where in wheres:
            for insecure in (False, True):
                owners = ('self', 'cross') if att is not None else ('self',)
                for owner in owners:
                    base = SCRATCHDIR / (f"{slug}-{owner}-{abspath}-{where}-"
                                         + ('ins' if insecure else 'safe'))
                    rmtree(base)
                    base.mkdir(parents=True)
                    ctx = types.SimpleNamespace(
                        base=base, outside=base / 'outside', plant=base / 'plant',
                        owner=owner, att_uid=att, abspath=abspath, where=where,
                        insecure=insecure)
                    ctx.outside.mkdir()
                    ctx.plant.mkdir()

                    def plant_link(at, target, _c=ctx):
                        os.symlink(target, at)
                        if _c.owner == 'cross':
                            os.lchown(at, _c.att_uid, _c.att_uid)
                    ctx.plant_link = plant_link

                    followed = bool(case(ctx))
                    expect = insecure or owner == 'self'
                    cell = f"{abspath} {where} {'insecure' if insecure else 'safe'}"
                    if followed and not expect:
                        test_fail(
                            f"{tag}: CROSS-UID {cell}: the planted symlink was "
                            "FOLLOWED (op escaped to outside/). An operator path "
                            "must refuse a symlink not owned by uid 0 or the euid.")
                    if not followed and expect:
                        why = ("--insecure-links did not restore symlink following"
                               if insecure else
                               "the operator's OWN (euid-owned) symlink was refused")
                        test_fail(f"{tag}: {('CROSS' if owner=='cross' else 'SAME')}"
                                  f"-UID {cell}: {why}.")
    if att is None and euid != 0:
        print(f"{tag}: same-uid cells confirmed; cross-uid cells need root (skipped)")


def plant_operator_symlink(ctx, rel_anchor, kind='dir'):
    """Plant this cell's option-path symlink and return (option_value, escape).

    option_value is what to feed the option: an absolute path, or a name
    relative to rel_anchor (the directory the option resolves a relative value
    against -- e.g. the destination dir for --backup-dir/--link-dest, or the cwd
    for --temp-dir).  escape is the out-of-tree object the operation acts on IF
    the symlink is followed.

    kind='dir'  (a directory option, e.g. --backup-dir/--temp-dir/--link-dest):
        leaf   -> the symlink itself is the dir; escape = ctx.outside.
        parent -> a parent component is the symlink; escape = ctx.outside/'sub'.
    kind='file' (a file option, e.g. --log-file/--files-from/--write-batch):
        leaf   -> the symlink targets the out-of-tree victim file directly.
        parent -> a parent component is the symlink; the leaf name is appended.
        escape = ctx.outside/'victim' either way.
    """
    base = ctx.plant if ctx.abspath == 'abs' else rel_anchor
    if kind == 'file':
        victim = ctx.outside / 'victim'
        if ctx.where == 'leaf':
            link = base / 'osl'
            ctx.plant_link(link, victim)
            return (str(link) if ctx.abspath == 'abs' else 'osl'), victim
        link = base / 'opd'
        ctx.plant_link(link, ctx.outside)
        return ((str(link / 'victim') if ctx.abspath == 'abs' else 'opd/victim'),
                victim)
    if ctx.where == 'leaf':
        link = base / 'osl'
        ctx.plant_link(link, ctx.outside)
        return (str(link) if ctx.abspath == 'abs' else 'osl'), ctx.outside
    link = base / 'opd'
    ctx.plant_link(link, ctx.outside)
    return ((str(link / 'sub') if ctx.abspath == 'abs' else 'opd/sub'),
            ctx.outside / 'sub')


# --- variety tree (cross-version regression coverage) ----------------------
# A "variety tree" exercises every inode type rsync handles (dirs, regular
# files, symlinks, fifos, sockets, char/block devices) with a spread of
# permissions, xattrs, ACLs and (as root) ownership, plus heavy symlink
# coverage: links to each type, links that escape a transfer root via ../..,
# absolute links, and links whose intermediate components transit outside the
# tree. It is the source for differential tests that assert the current binary
# produces the same destination tree as an old release (see variety_test.py).

def acls_supported() -> bool:
    """True if this rsync was built with ACL support AND this platform has a
    usable setfacl/getfacl (or macOS chmod +a). Mirrors xattrs_supported()."""
    vv = run_rsync('-VV', check=True, capture_output=True).stdout
    if '"ACLs": true' not in vv:
        return False
    if _SYSTEM in ('Linux', 'FreeBSD') or _CYGWIN:
        return (shutil.which('setfacl') is not None
                and shutil.which('getfacl') is not None)
    if _SYSTEM == 'Darwin':
        return shutil.which('chmod') is not None
    return False


@_functools.lru_cache(maxsize=1)
def devices_supported() -> bool:
    """True if device nodes can be created here.

    euid==0 is necessary but not sufficient: a user-namespaced container
    (rootless podman/docker, buildd chroots) reports euid==0 yet lacks
    CAP_MKNOD, so os.mknod() of a device fails EPERM.  Probe once."""
    if os.geteuid() != 0 or not hasattr(os, 'mknod'):
        return False
    with tempfile.TemporaryDirectory(prefix='rsync-devprobe.') as d:
        try:
            os.mknod(os.path.join(d, 'p'), 0o600 | stat.S_IFCHR, os.makedev(1, 3))
        except (PermissionError, OSError):
            return False
    return True


def hardlink_symlinks_supported(where=None) -> bool:
    """True if THIS FILESYSTEM can hard-link a symlink.

    rsync's own "hardlink_symlinks" capability is a build-time answer and says
    nothing about the filesystem underneath: HFS+ returns ENOTSUP for
    link()-ing a symlink where APFS and Linux succeed.  Probe where the test
    data will actually live, not where the source tree is."""
    d = tempfile.mkdtemp(prefix='rsync-hlsym.', dir=str(where) if where else None)
    try:
        link, hard = os.path.join(d, 's'), os.path.join(d, 'h')
        os.symlink('target-need-not-exist', link)
        try:
            os.link(link, hard, follow_symlinks=False)
        except NotImplementedError:
            return False
        except OSError as e:
            # Only "the filesystem cannot do this" answers the question.  EPERM,
            # ENOSPC, EMLINK, EIO or a quota refusal would otherwise be reported
            # as a capability difference and quietly reshape the caller's
            # expectations, so let those propagate.
            if e.errno in (errno.ENOTSUP, getattr(errno, 'EOPNOTSUPP', errno.ENOTSUP)):
                return False
            raise
        return True
    finally:
        shutil.rmtree(d, ignore_errors=True)


def owners_supported() -> bool:
    """True if the builder may assign mixed uid/gid (euid==0)."""
    return os.geteuid() == 0


def make_fifo(path) -> 'None':
    """Create a FIFO (named pipe) at `path`."""
    os.mkfifo(str(path))


def make_socket(path) -> 'None':
    """Create a UNIX-domain socket inode at `path`.

    The AF_UNIX sun_path is capped at ~108 bytes, which a depth-8 absolute path
    can overflow, so we chdir to the parent and bind the bare (short) basename,
    restoring the cwd in a finally. The bound inode persists as an S_IFSOCK on
    disk after the socket is closed."""
    path = Path(path)
    old = os.getcwd()
    s = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
    try:
        os.chdir(path.parent)
        s.bind(path.name)
    finally:
        s.close()
        os.chdir(old)


def make_device(path, kind: str, major: int, minor: int,
                mode: int = 0o644) -> 'None':
    """Create a char ('c') or block ('b') device node. Caller must gate on
    devices_supported() -- unprivileged this raises PermissionError."""
    fmt = stat.S_IFCHR if kind == 'c' else stat.S_IFBLK
    os.mknod(str(path), mode | fmt, os.makedev(major, minor))


def _acl_env() -> dict:
    """Environment for getfacl/setfacl: scrub POSIXLY_CORRECT (which alters
    some getfacl builds' flag/header semantics) and pin LC_ALL=C."""
    env = dict(os.environ)
    env.pop('POSIXLY_CORRECT', None)
    env['LC_ALL'] = 'C'
    return env


def acl_set(spec: str, path) -> bool:
    """Apply one ACL entry `spec` (e.g. 'u:0:rwx', 'g:0:r-x', 'd:u:0:rwx' for a
    directory default entry) to `path` via setfacl. Returns True on success,
    False if the filesystem rejects ACLs (EOPNOTSUPP) so the caller degrades
    gracefully. macOS is not driven here (returns False)."""
    if not (_SYSTEM in ('Linux', 'FreeBSD') or _CYGWIN):
        return False
    proc = subprocess.run(['setfacl', '-m', spec, str(path)],
                          capture_output=True, text=True, env=_acl_env())
    return proc.returncode == 0


def _acl_sig(path) -> str:
    """Path-free signature of a node's ACL entries, for comparing two trees.
    Strips getfacl's comment header (which embeds the path/owner) and sorts the
    entries so the result depends only on the access/default ACL, not on where
    the file lives. Empty when ACLs aren't readable here."""
    if not (_SYSTEM in ('Linux', 'FreeBSD') or _CYGWIN):
        return ''
    try:
        out = subprocess.run(['getfacl', str(path)], capture_output=True,
                             text=True, env=_acl_env()).stdout
    except OSError:
        return ''
    return ';'.join(sorted(l for l in out.splitlines()
                           if l.strip() and not l.startswith('#')))


def _xattr_sig(path) -> str:
    """Path-free signature of a node's user xattrs, for comparing two trees.
    Native on Linux (symmetric with xattr_set); getfattr with the '# file:'
    header stripped on Cygwin. Returns '' elsewhere -- xattr fidelity is gated
    on the Linux CI run, and tls still compares structure on every platform."""
    p = str(path)
    if _SYSTEM == 'Linux':
        try:
            names = sorted(n for n in os.listxattr(p, follow_symlinks=False)
                           if n.startswith('user.'))
        except OSError:
            return ''
        out = []
        for n in names:
            try:
                v = os.getxattr(p, n, follow_symlinks=False)
            except OSError:
                continue
            out.append(n + '=' + v.decode('utf-8', 'surrogateescape'))
        return ';'.join(out)
    if _CYGWIN:
        try:
            d = subprocess.check_output(
                ['getfattr', '--no-dereference', '-d', p],
                text=True, stderr=subprocess.DEVNULL)
        except (subprocess.CalledProcessError, OSError):
            return ''
        return ';'.join(sorted(l for l in d.splitlines()
                               if l and not l.startswith('# file:')))
    return ''


def _variety_fill(path, size: int, key: str) -> 'None':
    """Write `size` bytes of deterministic, non-trivial content derived from
    `key`, so a variety tree is byte-reproducible across separate builds."""
    import hashlib
    buf = bytearray()
    i = 0
    while len(buf) < size:
        buf += hashlib.sha256(f'{key}:{i}'.encode()).digest()
        i += 1
    with open(str(path), 'wb') as f:
        f.write(bytes(buf[:size]))


def _all_entries(root) -> list:
    """Every entry under `root` (the root dir, real subdirs, files, specials,
    and symlinks themselves) visited exactly once and WITHOUT following any
    symlink. For chown/utime finalisation that must not escape the tree."""
    root = Path(root)
    res = []
    for dp, dns, fns in os.walk(root):       # followlinks=False
        d = Path(dp)
        res.append(d)
        for n in fns:
            res.append(d / n)
        for n in dns:
            sub = d / n
            if sub.is_symlink():             # os.walk won't recurse into it
                res.append(sub)
    return res


def make_variety_tree(root, *, depth: int = 8, with_acls=None, with_xattrs=None,
                      with_devices=None, with_owners=None,
                      seed: int = 0x5A17) -> dict:
    """Build a deterministic 'variety tree' rooted at `root`.

    Capability args default to None => auto-detect (xattrs_supported() etc.).
    Tests pass EXPLICIT bools so the current- and old-binary source trees are
    built with identical capabilities, keeping the differential comparison
    apples-to-apples. Re-runnable: rmtree(root) first; `seed` drives only fixed
    choices (no time/pid randomness) so two calls yield identical trees.

    Layout (caller transfers root/transfer_root/):
      root/above/        real nodes ABOVE the transfer root (escape targets)
      root/transfer_root d0..d{depth-1} backbone; at each level a bouquet of
                         every type + a symlink to every type; plus abs_links/
                         (absolute links) and escape/ (../.. links that leave
                         the transfer root, one per above-root type).

    Returns {'transfer_root': Path, 'above_targets': {type: Path},
             'counts': {type: n}}.
    """
    root = Path(root)
    rmtree(root)
    if with_xattrs is None:
        with_xattrs = xattrs_supported()
    if with_acls is None:
        with_acls = acls_supported()
    if with_devices is None:
        with_devices = devices_supported()
    if with_owners is None:
        with_owners = owners_supported()

    root.mkdir(parents=True)
    above = root / 'above'
    above.mkdir()
    troot = root / 'transfer_root'
    troot.mkdir()

    counts = {}
    def bump(t):
        counts[t] = counts.get(t, 0) + 1

    perm_cycle = [0o400, 0o640, 0o644, 0o600, 0o755]

    def reg(p, size, mode=0o644):
        _variety_fill(p, size, f'{seed:x}:{os.path.relpath(p, root)}')
        os.chmod(p, mode)
        bump('file')
        return p

    def mkdir1(p, mode=None):
        p.mkdir()
        if mode is not None:
            os.chmod(p, mode)
        bump('dir')
        return p

    def lnk(target, p):
        os.symlink(target, p)
        bump('symlink')
        return p

    # --- above-root real targets (escape/ links point here) ---
    above_targets = {}
    above_targets['dir'] = mkdir1(above / 'a_dir')
    reg(above / 'a_dir' / 'inner', 256)
    above_targets['file'] = reg(above / 'a_file', 8192)
    above_targets['fifo'] = above / 'a_fifo'; make_fifo(above_targets['fifo']); bump('fifo')
    above_targets['sock'] = above / 'a_sock'; make_socket(above_targets['sock']); bump('socket')
    if with_devices:
        above_targets['dev'] = above / 'a_dev_c'
        make_device(above_targets['dev'], 'c', 1, 3); bump('device')
        make_device(above / 'a_dev_b', 'b', 7, 0); bump('device')
    above_targets['link'] = lnk('a_file', above / 'a_link')

    # --- depth backbone with a bouquet at each level ---
    cur = troot
    for n in range(depth):
        reg(cur / f'f{n}', 1024 * (n + 1))
        if n % 2 == 0:                           # hard-link coverage for -H
            os.link(cur / f'f{n}', cur / f'hl{n}')
            bump('hardlink')
        reg(cur / f'perm{n}', 700, perm_cycle[(seed + n) % len(perm_cycle)])
        reg(cur / f'setuid{n}', 512, 0o4755)
        mkdir1(cur / f'setgid{n}', 0o2775)
        mkdir1(cur / f'sticky{n}', 0o1777)
        for k in range(3):                       # widen toward ~200 entries
            reg(cur / f'g{n}_{k}', 300)
        make_fifo(cur / f'fifo{n}'); bump('fifo')
        make_socket(cur / f'sk{n}'); bump('socket')
        if with_devices:
            make_device(cur / f'cdev{n}', 'c', 1, 5); bump('device')
            make_device(cur / f'bdev{n}', 'b', 7, n); bump('device')
        # symlink bouquet: one link to each type present at this level
        lnk(f'f{n}', cur / f'ln2file{n}')
        lnk(f'fifo{n}', cur / f'ln2fifo{n}')
        lnk(f'sk{n}', cur / f'ln2sock{n}')
        if with_devices:
            lnk(f'cdev{n}', cur / f'ln2dev{n}')
        lnk(f'ln2file{n}', cur / f'ln2ln{n}')     # link to a symlink
        lnk(f'nonexistent_{n}', cur / f'dangling{n}')
        if n < depth - 1:
            nxt = mkdir1(cur / f'd{n + 1}')
            lnk(f'd{n + 1}', cur / f'ln2dir{n}')  # link to a directory
            cur = nxt

    # --- absolute-path links (targets encode the source scratch path) ---
    absd = mkdir1(troot / 'abs_links')
    lnk(str((troot / 'f0').resolve()), absd / 'abs_file')
    lnk(str(above_targets['file'].resolve()), absd / 'abs_above')

    # --- escaping (unsafe) links: inside the transfer root, target outside ---
    escd = mkdir1(troot / 'escape')
    lnk('../../above/a_dir', escd / 'esc_dir')
    lnk('../../above/a_file', escd / 'esc_file')
    lnk('../../above/a_fifo', escd / 'esc_fifo')
    lnk('../../above/a_sock', escd / 'esc_sock')
    if with_devices:
        lnk('../../above/a_dev_c', escd / 'esc_dev')
    lnk('../../above/a_link', escd / 'esc_link')
    # intermediate component transits OUTSIDE the whole tree, then back in
    lnk(f'../../../{root.name}/above/a_file', escd / 'esc_deep')

    # --- xattrs on dirs + regular files (symlink xattrs are unsupported on
    #     Linux, so skip them) ---
    if with_xattrs:
        for p in walk_dirs(troot) + walk_files(troot):
            try:
                xattr_set('variety', os.path.basename(str(p)), p)
            except OSError:
                pass

    # --- ACLs on a deterministic subset ---
    if with_acls:
        dirs = walk_dirs(troot)
        files = walk_files(troot)
        for i, d in enumerate(dirs):
            if i % 3 == 0:
                acl_set('u:0:rwx', d)
            if i % 5 == 0:
                acl_set('d:u:0:rwx', d)      # directory default entry
        for i, f in enumerate(files):
            if i % 4 == 0:
                acl_set('g:0:r-x', f)

    entries = sorted(_all_entries(root), key=lambda x: str(x))

    # --- mixed ownership (root only), including symlinks ---
    if with_owners:
        idset = [(0, 0), (1, 1), (2, 2)]
        for i, p in enumerate(entries):
            uid, gid = idset[(seed + i) % len(idset)]
            try:
                os.chown(str(p), uid, gid, follow_symlinks=False)
            except OSError:
                pass

    # --- deterministic, varied mtimes (last, so nothing resets them); makes
    #     the tls listing reproducible across separate builds ---
    base = 1_000_000_000
    for i, p in enumerate(entries):
        t = base + (i * 7) % 1_000_000
        try:
            os.utime(str(p), (t, t), follow_symlinks=False)
        except (OSError, NotImplementedError, ValueError):
            if not os.path.islink(str(p)):
                try:
                    os.utime(str(p), (t, t))
                except OSError:
                    pass

    return {'transfer_root': troot, 'above_targets': above_targets,
            'counts': counts}


def _rel_nonlink_entries(root) -> list:
    """Relative paths of every non-symlink entry (real dirs + non-symlink
    files/specials) under `root`, sorted. Used to compare per-entry metadata
    without following or descending into symlinks."""
    root = Path(root)
    res = []
    for dirpath, _dirnames, filenames in os.walk(root):  # followlinks=False
        d = Path(dirpath)
        if d != root:
            res.append(d.relative_to(root))
        for fn in filenames:
            fp = d / fn
            if not fp.is_symlink():
                res.append(fp.relative_to(root))
    return sorted(res, key=lambda p: str(p))


def _safe_walk_files(root) -> list:
    """walk_files() variant that tolerates unreadable directories (skips them
    instead of raising), for comparing trees whose mixed/foreign ownership can
    leave some entries inaccessible to the current user."""
    root = Path(root)
    res = []
    for dp, _dns, fns in os.walk(root):   # onerror=None -> unreadable dirs skipped
        d = Path(dp)
        for n in fns:
            p = d / n
            try:
                if p.is_file() and not p.is_symlink():
                    res.append(p)
            except OSError:
                continue
    return sorted(res, key=lambda x: str(x))


def compare_trees(a, b, label: str = '', *,
                          with_acls: bool = True,
                          with_xattrs: bool = True) -> list:
    """Compare two trees WITHOUT ever opening a fifo/socket/device as a
    stream. Returns a list of human-readable difference strings ([] == match);
    the caller decides whether a difference is a fail or an xfail.

    Checks: (1) the tls listings (type+mode+owner+size+mtime+symlink target for
    every inode); (2) byte-equality of regular files by relative path; (3) user
    xattrs; (4) POSIX ACLs. Never uses `diff -r` (it blocks on specials)."""
    a = Path(a)
    b = Path(b)
    pre = f"{label}: " if label else ""
    diffs = []

    la = rsync_ls_lR(a)
    lb = rsync_ls_lR(b)
    if la != lb:
        import difflib
        ud = ''.join(difflib.unified_diff(
            la.splitlines(keepends=True), lb.splitlines(keepends=True),
            fromfile=f'{a} (tls)', tofile=f'{b} (tls)'))
        diffs.append(f"{pre}tls listings differ:\n{ud}")

    files_a = sorted(p.relative_to(a) for p in _safe_walk_files(a))
    files_b = sorted(p.relative_to(b) for p in _safe_walk_files(b))
    set_b = set(files_b)
    if set(files_a) != set_b:
        only_a = sorted(str(p) for p in set(files_a) - set_b)
        only_b = sorted(str(p) for p in set_b - set(files_a))
        diffs.append(f"{pre}regular-file set differs: "
                     f"only in a={only_a} only in b={only_b}")
    for rel in files_a:
        if rel not in set_b:
            continue
        try:
            same = filecmp.cmp(str(a / rel), str(b / rel), shallow=False)
        except OSError as e:
            diffs.append(f"{pre}cannot compare contents of {rel} "
                         f"(permission denied?): {e}")
            continue
        if not same:
            diffs.append(f"{pre}content differs: {rel}")

    # hard-link grouping (catches an -H divergence the tls listing can't show)
    def _hl_groups(rootp):
        from collections import defaultdict
        ino = defaultdict(list)
        for p in _safe_walk_files(rootp):
            try:
                st = p.stat()
            except OSError:
                continue
            if st.st_nlink > 1:
                ino[(st.st_dev, st.st_ino)].append(str(p.relative_to(rootp)))
        return sorted(tuple(sorted(v)) for v in ino.values() if len(v) > 1)
    ga, gb = _hl_groups(a), _hl_groups(b)
    if ga != gb:
        diffs.append(f"{pre}hard-link grouping differs: a={ga} b={gb}")

    if with_xattrs or with_acls:
        for rel in _rel_nonlink_entries(a):
            pa = a / rel
            pb = b / rel
            if not pb.exists():
                continue
            if with_xattrs:
                xa, xb = _xattr_sig(pa), _xattr_sig(pb)
                if xa != xb:
                    diffs.append(f"{pre}xattr differs: {rel} "
                                 f"(a={xa!r} b={xb!r})")
            if with_acls:
                aa, ab = _acl_sig(pa), _acl_sig(pb)
                if aa != ab:
                    diffs.append(f"{pre}ACL differs: {rel} "
                                 f"(a={aa!r} b={ab!r})")

    return diffs


def assert_trees_equal(a, b, label: str = '', **kwargs) -> 'None':
    """compare_trees(); test_fail() on any difference."""
    diffs = compare_trees(a, b, label, **kwargs)
    if diffs:
        test_fail('\n'.join(diffs))

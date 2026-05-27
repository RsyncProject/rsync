"""Shared helpers for rsync's Python test scripts.

This is the Python counterpart of testsuite/rsync.fns. It exposes only what
the Python-rewritten tests actually need; grow it as more shell tests are
ported.

Conventions matching the shell harness:
  * Exit 0 = pass, 1 = fail, 77 = skip, 78 = xfail.
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
import os
import platform
import shlex
import shutil
import socket as _socket
import stat
import subprocess
import sys
import time
from pathlib import Path


# --- environment -----------------------------------------------------------

def _required(name: str) -> str:
    v = os.environ.get(name)
    if not v:
        sys.stderr.write(
            f"rsyncfns: required environment variable {name} is not set; "
            "run this test via runtests.py rather than directly.\n"
        )
        sys.exit(2)
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

# TLS_ARGS controls how the 'tls' helper formats listings (e.g. --atimes,
# -l, -L). Tests that exercise non-default rsync features (atimes, etc.)
# assign to rsyncfns.TLS_ARGS before calling checkit / rsync_ls_lR.
TLS_ARGS = os.environ.get('TLS_ARGS', '')

# Daemon-mode transport. The DEFAULT is the secure stdio-pipe mechanism
# (RSYNC_CONNECT_PROG), which opens no listening socket at all. The runner
# sets RSYNC_TEST_USE_TCP=1 only when invoked with --use-tcp, which switches
# daemon tests to a real rsyncd bound to loopback (see start_test_daemon).
USE_TCP = os.environ.get('RSYNC_TEST_USE_TCP') == '1'

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
    sys.exit(1)


def test_skipped(msg: str) -> 'None':
    sys.stderr.write(msg.rstrip() + '\n')
    (TMPDIR / 'whyskipped').write_text(msg.rstrip() + '\n')
    sys.exit(77)


def test_xfail(msg: str) -> 'None':
    sys.stderr.write(msg.rstrip() + '\n')
    sys.exit(78)


# --- rsync invocation ------------------------------------------------------

# --- TCP port coordination across parallel tests ---------------------------

_PORT_LOCK_PATH = '/tmp/rsync_test.lck'
_port_lock_fd = None


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
    return fd


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
    global _port_lock_fd
    if _port_lock_fd is None:
        _port_lock_fd = _open_lock_file()
    for port in sorted(ports):
        # F_SETLKW via fcntl.lockf(LOCK_EX, length, start): exclusive
        # byte-range lock on byte `port`, blocking until acquired.
        fcntl.lockf(_port_lock_fd, fcntl.LOCK_EX, 1, port)


# --- standalone rsyncd helpers ---------------------------------------------

def _set_pdeathsig() -> 'None':
    """Linux: ask the kernel to send SIGTERM to us if our parent dies.
    A no-op on every other platform. Used as preexec_fn so a kill -9 of
    the test process doesn't strand the rsyncd we spawned."""
    if not sys.platform.startswith('linux'):
        return
    try:
        import ctypes
        libc = ctypes.CDLL('libc.so.6', use_errno=True)
        PR_SET_PDEATHSIG = 1
        libc.prctl(PR_SET_PDEATHSIG, 15, 0, 0, 0)  # 15 == SIGTERM
    except OSError:
        pass


def _stop_rsyncd(proc) -> 'None':
    if proc.poll() is not None:
        return
    try:
        proc.terminate()
        proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
            proc.wait(timeout=1)
        except (subprocess.TimeoutExpired, OSError):
            pass


def start_rsyncd(conf_path, port: int) -> 'subprocess.Popen':
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

    This is only ever reached from start_test_daemon() in --use-tcp mode; the
    default (pipe) mode never starts a listening daemon.
    """
    argv = shlex.split(RSYNC) + [
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
    atexit.register(_stop_rsyncd, proc)

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


def start_test_daemon(conf_path, port: int) -> str:
    """Bring up the test daemon and return a URL prefix for client commands.

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
    if USE_TCP:
        claim_ports(port)
        start_rsyncd(conf_path, port)
        return f'rsync://localhost:{port}/'
    os.environ['RSYNC_CONNECT_PROG'] = f'{RSYNC} --config={conf_path} --daemon'
    return 'rsync://localhost/'


def require_tcp(reason: str) -> 'None':
    """Skip the test (exit 77) unless we're in --use-tcp mode. For tests that
    fundamentally need a real listening socket / TCP peer and have no secure
    pipe equivalent (the fake-proxy listener; the reverse-DNS hostname-ACL
    daemon test)."""
    if not USE_TCP:
        test_skipped(reason)


def rsync_argv(*args: str) -> list:
    """Return the argv for invoking rsync with the given extra arguments.

    RSYNC may be a multi-word command (e.g. 'valgrind ... /build/rsync'); we
    shlex-split it so subprocess sees a proper argv list. Each *args entry
    is appended verbatim, so callers should pass tokens already split (no
    embedded option/value joined by spaces).
    """
    return shlex.split(RSYNC) + list(args)


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


def xattr_set(name: str, value: str, *paths) -> 'None':
    """Set the user-namespace xattr `name` (logical) = `value` on each path."""
    full = _xattr_full(name)
    for p in paths:
        p = str(p)
        if _SYSTEM == 'Linux':
            os.setxattr(p, full.encode(), value.encode())
        elif _CYGWIN:
            subprocess.run(['setfattr', '-n', full, '-v', value, p],
                           check=True)
        elif _SYSTEM == 'Darwin':
            subprocess.run(['xattr', '-w', full, value, p], check=True)
        elif _SYSTEM == 'FreeBSD':
            subprocess.run(['setextattr', '-h', 'user', full, value, p],
                           check=True)
        elif _SYSTEM == 'SunOS':
            # Solaris extended attributes are a per-file namespace; runat
            # cd's into it and runs a shell that reads the script on stdin
            # (the -c form mangles args). Pass name/value via the environment
            # to dodge quoting; printf writes the value with no trailing
            # newline, matching the byte-exact value other platforms store.
            subprocess.run(
                ['runat', p, '/bin/sh'],
                input='printf %s "$XVAL" > "$XNAME"\n', text=True,
                env={**os.environ, 'XNAME': full, 'XVAL': value}, check=True)
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
    proc = subprocess.run(['sh', '-c', cmd], capture_output=True,
                          text=True, cwd=str(directory))
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


_rb_cache = None


def resolve_beneath_supported() -> bool:
    """True if this rsync can FOLLOW an in-tree directory symlink under its
    secure resolver -- i.e. update a file through a dir-symlink on the receiver
    (--keep-dirlinks; issue #715).

    False wherever the portable per-component O_NOFOLLOW fallback is the active
    resolver: a platform with no kernel "beneath" primitive, Linux < 5.6, a
    seccomp-blocked openat2, or a --disable-openat2 build. There the delta
    update through the symlinked directory fails verification. Probed
    functionally (an initial transfer plus a delta update through a dir-symlink)
    so it tracks the actual binary rather than a platform name, and cached."""
    global _rb_cache
    if _rb_cache is not None:
        return _rb_cache
    probe = SCRATCHDIR / '.rb_probe'
    rmtree(probe)
    (probe / 'home' / 'real').mkdir(parents=True)
    os.symlink('real', probe / 'home' / 'link')
    (probe / 'src' / 'link').mkdir(parents=True)
    f = probe / 'src' / 'link' / 'f'
    make_data_file(f, 40000)

    def push():
        subprocess.run(
            rsync_argv('-KRl', '--no-whole-file', 'link/f',
                       f"{probe / 'home'}/"),
            cwd=str(probe / 'src'),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    push()
    with open(f, 'ab') as fh:           # size change -> forces a delta update
        fh.write(b'appended tail for delta\n')
    push()
    dst = probe / 'home' / 'real' / 'f'
    _rb_cache = dst.is_file() and filecmp.cmp(str(f), str(dst), shallow=False)
    rmtree(probe)
    return _rb_cache


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

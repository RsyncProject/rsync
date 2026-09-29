"""Shared helpers for the Windows test suite.

testsuite/ is written against Unix semantics -- ACLs, xattrs, uid/gid,
devices, a POSIX shell for the remote-shell stand-in -- so most of it cannot
run here, and bending it into shape would mean editing shared test code for
one platform's benefit.  This suite instead covers what the Windows port
actually supports, and skips (rather than fails) what Windows genuinely
cannot do.

Exit codes match rsync's own convention so the two suites read alike:
0 = pass, 1 = fail, 77 = skip.

Copyright (C) 2026 Max Vilimpoc, rsync CMake/Windows port.
Distributed under the same GPL-3.0-or-later terms as the rest of rsync.
"""

import hashlib
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

PASS, FAIL, SKIP = 0, 1, 77


def _required(name):
    value = os.environ.get(name)
    if not value:
        sys.stderr.write(
            f"wintest: {name} is not set; run this via win32/tests/run.py\n")
        sys.exit(2)
    return value


RSYNC = Path(_required('WINTEST_RSYNC'))       # the rsync.exe under test
TOOLDIR = Path(_required('WINTEST_TOOLDIR'))   # where the helpers live
SRCDIR = Path(_required('WINTEST_SRCDIR'))     # the rsync source tree
SCRATCH = Path(_required('WINTEST_SCRATCH'))   # this test's scratch dir

FROM = SCRATCH / 'from'
TO = SCRATCH / 'to'


# --- reporting -------------------------------------------------------------

def fail(msg):
    sys.stderr.write(msg.rstrip() + '\n')
    sys.exit(FAIL)


def skip(msg):
    sys.stderr.write('SKIP: ' + msg.rstrip() + '\n')
    sys.exit(SKIP)


def ok(msg=''):
    if msg:
        print(msg)
    sys.exit(PASS)


def check(condition, msg):
    if not condition:
        fail(msg)


# --- running rsync ---------------------------------------------------------

def rsync(*args, expect=0, capture=True, stdin_text=None):
    """Run the rsync under test.  Fails the test on an unexpected exit code.

    `expect` may be an int or a tuple; pass None to accept anything.
    `stdin_text` feeds the process's stdin, which --files-from=- needs.
    """
    argv = [str(RSYNC), *[str(a) for a in args]]
    print('rsync ' + ' '.join(str(a) for a in args))
    # rsync here speaks UTF-8 (the embedded manifest selects that code
    # page), so decode as UTF-8 rather than the console's ANSI default.
    proc = subprocess.run(argv, capture_output=capture, text=True,
                          encoding='utf-8', errors='replace',
                          input=stdin_text)
    if capture and proc.stdout:
        print(proc.stdout, end='')
    if capture and proc.stderr:
        sys.stderr.write(proc.stderr)
    if expect is not None:
        codes = expect if isinstance(expect, tuple) else (expect,)
        if proc.returncode not in codes:
            fail(f"rsync exited {proc.returncode}, expected {codes}")
    return proc


def helper(name, *args, expect=0):
    """Run one of the C test helpers (tls, wildtest, t_unsafe, ...)."""
    exe = TOOLDIR / (name + ('.exe' if os.name == 'nt' else ''))
    if not exe.is_file():
        skip(f"helper {name} was not built")
    proc = subprocess.run([str(exe), *[str(a) for a in args]],
                          capture_output=True, text=True,
                          encoding='utf-8', errors='replace')
    if expect is not None and proc.returncode != expect:
        fail(f"{name} exited {proc.returncode}, expected {expect}\n"
             f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}")
    return proc


# --- file/tree utilities ---------------------------------------------------

def write(path, content, mtime=None):
    """Create a file (and any parent dirs) with the given text or bytes."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content, encoding='utf-8')
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


_next_mtime = [int(time.time()) - 3600]


def rewrite(path, content):
    """Overwrite a file, advancing its mtime.

    rsync's quick check is size plus mtime, so a same-size edit made within
    the same second looks unchanged and is skipped -- correct behaviour, but
    it makes a test that rewrites a file in place silently do nothing.
    Stepping the mtime keeps such tests honest without resorting to
    --checksum or a sleep.
    """
    _next_mtime[0] += 2
    return write(path, content, mtime=_next_mtime[0])


def pseudo_random(size, seed=1):
    """Deterministic bytes -- good enough to defeat the delta algorithm's
    block matching where a test wants real content."""
    out = bytearray(size)
    state = seed & 0xFFFFFFFF
    for i in range(size):
        state = (1103515245 * state + 12345) & 0xFFFFFFFF
        out[i] = (state >> 16) & 0xFF
    return bytes(out)


def manifest(root):
    """{relative path: md5} for every file under `root`, plus a marker for
    each directory, so a comparison catches structure as well as content."""
    root = Path(root)
    entries = {}
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = Path(dirpath).relative_to(root)
        for name in dirnames:
            key = (rel_dir / name).as_posix()
            entries[key] = '<dir>'
        for name in filenames:
            full = Path(dirpath) / name
            key = (rel_dir / name).as_posix()
            entries[key] = hashlib.md5(full.read_bytes()).hexdigest()
    return entries


def compare_trees(a, b, label='trees'):
    """Fail unless two trees hold the same names and the same content."""
    ma, mb = manifest(a), manifest(b)
    if ma == mb:
        return

    only_a = sorted(set(ma) - set(mb))
    only_b = sorted(set(mb) - set(ma))
    differ = sorted(k for k in set(ma) & set(mb) if ma[k] != mb[k])

    lines = [f"{label} differ:"]
    lines += [f"  only in {a}: {p}" for p in only_a[:20]]
    lines += [f"  only in {b}: {p}" for p in only_b[:20]]
    lines += [f"  content differs: {p}" for p in differ[:20]]
    fail('\n'.join(lines))


def make_test_tree(root):
    """A small tree with the awkward names the port has to cope with."""
    root = Path(root)
    write(root / 'plain.txt', 'plain content\n')
    write(root / 'a file with spaces.txt', 'spaces\n')
    write(root / "odd$name'q.txt", 'metacharacters\n')
    write(root / 'naive-cafe.txt', 'ascii name\n')
    write(root / 'sub' / 'nested.txt', 'nested\n')
    write(root / 'sub' / 'deeper' / 'deep.txt', 'deep\n')
    write(root / 'binary.bin', pseudo_random(100000))
    return root


# --- capability probes -----------------------------------------------------

def rsync_capabilities():
    """The words rsync --version reports, e.g. 'symlinks', 'no hardlinks'."""
    out = subprocess.run([str(RSYNC), '--version'],
                         capture_output=True, text=True).stdout
    caps = set()
    started = False
    for line in out.splitlines():
        if line.startswith('Capabilities:'):
            started = True
            continue
        if started:
            if not line.startswith(' '):
                break
            for item in line.split(','):
                item = item.strip().rstrip('.')
                if item:
                    caps.add(item)
    return caps


def have_capability(name):
    caps = rsync_capabilities()
    return name in caps and f'no {name}' not in caps


def can_create_symlinks():
    """Windows needs Developer Mode or SeCreateSymbolicLinkPrivilege."""
    probe = SCRATCH / '.symlink-probe'
    target = SCRATCH / '.symlink-target'
    target.mkdir(exist_ok=True)
    try:
        os.symlink(str(target), str(probe), target_is_directory=True)
    except (OSError, NotImplementedError, AttributeError):
        return False
    finally:
        if os.path.islink(str(probe)):
            os.remove(str(probe))
    return True


def make_junction(link, target):
    """A directory junction -- the one kind of reparse point Windows lets an
    unprivileged process create, which is enough to exercise readlink()."""
    res = subprocess.run(['cmd', '/c', 'mklink', '/J', str(link), str(target)],
                         capture_output=True, text=True)
    return res.returncode == 0


def rmtree(path):
    """Delete a tree, clearing the read-only bit and retrying briefly: a
    just-exited rsync may still hold a handle open on Windows."""
    def force_writable(func, p, _exc):
        try:
            os.chmod(p, 0o700)
            func(p)
        except OSError:
            pass

    for attempt in range(5):
        if not os.path.exists(path):
            return
        try:
            shutil.rmtree(path, onerror=force_writable)
            return
        except OSError:
            time.sleep(0.2)

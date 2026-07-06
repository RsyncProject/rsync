#!/usr/bin/env python3
"""abdiff.py -- differential A/B regression hunter for rsync.

Runs the same transfer with two rsync binaries (A = the build under test, e.g.
./rsync; B = a baseline, e.g. old_versions/rsync_3.4.1) and compares the
OUTCOME: exit code, error output, --stats "Literal data", the destination tree
(content + full metadata), and the --itemize change list.

Core oracle: for a BENIGN input a correctness/behaviour change between the two
builds must be invisible, so A and B must produce an identical destination tree
and both exit 0.  Any divergence is a regression candidate (e.g. a refactor that
silently changes what a benign `rsync -a` transfers).

This is a developer tool, NOT a runtests.py test (it does not end in _test.py and
imports nothing from the test harness).  Findings are printed and appended to a
log; minimize each into a testsuite/*_test.py.

Usage:
    testsuite/abdiff.py [--rsync-a ./rsync] [--rsync-b old_versions/rsync_3.4.1]
                      [--sweep options|pathshape|all] [--workdir DIR] [--keep]
                      [--findings abdiff-findings.txt] [--only NAME] [--list]
Exit 0 iff no regression candidates were found.
"""

from __future__ import annotations

import argparse
import hashlib
import itertools
import os
import random
import re
import shutil
import signal
import stat
import subprocess
import sys
import threading
import time
from concurrent.futures import (FIRST_COMPLETED, ThreadPoolExecutor,
                                as_completed, wait)
from pathlib import Path

# ---------------------------------------------------------------------------
# config / globals (set in main)
RSYNC_A = "./rsync"
RSYNC_B = "old_versions/rsync_3.4.1"
RRSYNC_A = None     # rrsync wrapper paired with A/B (None -> in-tree support/rrsync)
RRSYNC_B = None
KEEP = False
REPEAT = 2   # stability gate: run each binary N times; flaky scenarios quarantined
CMD_TIMEOUT = 120   # per-subprocess wall-clock guard, seconds (0 = unlimited)
COST = False        # --cost: also compare peak process-group RSS (resource oracle)
SCALE_N = 2000      # --scale: element count for the scale-escalation fixtures
_supports_cache: dict = {}
_supports_lock = threading.Lock()
_tls = threading.local()   # per-worker: .measure (bool) + .rss (peak bytes)
_PAGE = os.sysconf("SC_PAGE_SIZE")


def _group_rss(pgid):
    """Summed RSS (bytes) of every live process in process group pgid -- catches
    rsync's forked generator/receiver/sender (and ssh/daemon children), which a
    direct-child measure (/usr/bin/time) would miss."""
    total = 0
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            with open(f"/proc/{pid}/stat") as f:
                fields = f.read().split()
            if int(fields[4]) != pgid:        # field 5 (0-idx 4) = pgrp
                continue
            with open(f"/proc/{pid}/statm") as f:
                total += int(f.read().split()[1]) * _PAGE   # resident pages
        except (OSError, ValueError, IndexError):
            continue
    return total


def sh(cmd, cwd=None, env=None, timeout=None):
    """Run cmd capturing stdout/stderr. Runs in its own process group with a
    wall-clock timeout so a wedged rsync (or its ssh/daemon children) can't hang a
    worker forever -- on timeout the whole group is killed and rc 124 returned."""
    if timeout is None:
        timeout = CMD_TIMEOUT
    p = subprocess.Popen(cmd, cwd=cwd, env=env, stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, text=True, start_new_session=True)
    # cost oracle: while the transfer runs, sample the peak summed RSS of its
    # whole process group (set per-worker by one_run via _tls.measure).
    measure = getattr(_tls, "measure", False)
    stop = peak = poller = None
    if measure:
        peak = [0]
        stop = threading.Event()
        pgid = os.getpgid(p.pid)

        def _poll():
            while not stop.is_set():
                peak[0] = max(peak[0], _group_rss(pgid))
                stop.wait(0.03)
        poller = threading.Thread(target=_poll, daemon=True)
        poller.start()
    try:
        out, err = p.communicate(timeout=timeout or None)
        rc = p.returncode
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(p.pid), signal.SIGKILL)
        except OSError:
            pass
        out, err = p.communicate()
        rc = 124
        err = "[abdiff: TIMEOUT]\n" + (err or "")
    if measure:
        stop.set()
        poller.join(timeout=1)
        _tls.rss = peak[0] or None
    return subprocess.CompletedProcess(cmd, rc, out or "", err or "")


def supports(binary, opt):
    key = (binary, opt)
    with _supports_lock:
        if key in _supports_cache:
            return _supports_cache[key]
    r = sh([binary, opt, "--version"])
    bad = any(m in (r.stderr or "").lower()
              for m in ("unknown option", "unrecognized option", "no such option"))
    val = not bad
    with _supports_lock:
        _supports_cache[key] = val
    return val


# ---------------------------------------------------------------------------
# tree snapshot + comparison

ERR_MARKERS = ("rsync error", "failed to open", "rsync: ", "Invalid argument",
               "No such file", "Operation not permitted", "cannot ")

# Intentional, documented behaviour-change refusals (A errors where B didn't),
# recorded as ALLOW rather than a silent regression. Each entry is
# (substring-in-A's-stderr, human note). Populate as deliberate behaviour changes
# between the two builds are identified.
ALLOWLIST = []


def _xattrs(path):
    try:
        names = sorted(os.listxattr(path, follow_symlinks=False))
    except (OSError, AttributeError):
        return {}
    out = {}
    for n in names:
        if n.startswith("system.posix_acl_"):
            continue  # captured via getfacl
        try:
            out[n] = os.getxattr(path, n, follow_symlinks=False).hex()
        except OSError:
            out[n] = "?"
    return out


def _acl(path, is_dir):
    r = sh(["getfacl", "-pcEn", path]) if shutil.which("getfacl") else None
    if not r or r.returncode != 0:
        return None
    lines = [ln for ln in r.stdout.splitlines() if ln and not ln.startswith("#")]
    return "\n".join(sorted(lines)) or None


def snapshot(root: Path):
    """Map rel-path -> attribute dict for every entry under root (root itself
    excluded). Symlinks/specials are recorded, never followed."""
    root = Path(root)
    snap = {}
    inode_of = {}  # (dev,ino) -> first rel path, for hardlink grouping
    if not root.exists():
        return snap
    stack = [root]
    while stack:
        d = stack.pop()
        try:
            entries = sorted(os.scandir(d), key=lambda e: e.name)
        except OSError:
            continue
        for e in entries:
            p = Path(e.path)
            rel = str(p.relative_to(root))
            try:
                st = os.lstat(p)
            except OSError:
                snap[rel] = {"type": "GONE"}
                continue
            m = st.st_mode
            a = {
                "mode": stat.S_IMODE(m),
                "uid": st.st_uid, "gid": st.st_gid,
                "mtime": int(st.st_mtime),
            }
            if stat.S_ISDIR(m):
                a["type"] = "d"
                stack.append(p)
            elif stat.S_ISLNK(m):
                a["type"] = "l"
                a["target"] = os.readlink(p)
            elif stat.S_ISREG(m):
                a["type"] = "f"
                a["size"] = st.st_size
                a["blocks"] = st.st_blocks  # sparseness
                # "is this file hardlinked at all" (link-dest / -H) -- a robust
                # boolean; raw nlink counts are contaminated when A and B share a
                # --link-dest basis dir, but "copied(1) vs linked(>1)" still
                # catches a real link-dest/hardlink regression.
                a["linked"] = st.st_nlink > 1
                if st.st_nlink > 1:
                    key = (st.st_dev, st.st_ino)
                    a["hardlink"] = inode_of.setdefault(key, rel)
                h = hashlib.sha256()
                try:
                    with open(p, "rb") as fh:
                        for chunk in iter(lambda: fh.read(1 << 20), b""):
                            h.update(chunk)
                    a["sha"] = h.hexdigest()
                except OSError as ex:
                    a["sha"] = f"ERR:{ex.errno}"
            elif stat.S_ISFIFO(m):
                a["type"] = "p"
            elif stat.S_ISSOCK(m):
                a["type"] = "s"
            elif stat.S_ISBLK(m) or stat.S_ISCHR(m):
                a["type"] = "b" if stat.S_ISBLK(m) else "c"
                a["rdev"] = (os.major(st.st_rdev), os.minor(st.st_rdev))
            else:
                a["type"] = "?"
            xa = _xattrs(p)
            if xa:
                a["xattr"] = xa
            ac = _acl(p, stat.S_ISDIR(m))
            if ac:
                a["acl"] = ac
            snap[rel] = a
    return snap


# which attrs are meaningful depends on the options used; keep it simple and
# compare everything, but let callers ignore mtime when -t isn't in play, or
# for a type whose times rsync intentionally leaves unmanaged (-O dirs, -J
# symlinks) -> those dest mtimes are creation-time and differ between runs.
def diff_snapshots(sa, sb, ignore_mtime=False, ignore_mtime_types=()):
    diffs = []
    for rel in sorted(set(sa) | set(sb)):
        a, b = sa.get(rel), sb.get(rel)
        if a is None:
            diffs.append(f"  only in B(baseline): {rel} ({b.get('type')})")
            continue
        if b is None:
            diffs.append(f"  only in A(under-test): {rel} ({a.get('type')})")
            continue
        for k in sorted(set(a) | set(b)):
            if k == "mtime" and (ignore_mtime
                                 or a.get("type") in ignore_mtime_types):
                continue
            if a.get(k) != b.get(k):
                diffs.append(f"  {rel}: {k}  A={a.get(k)!r}  B={b.get(k)!r}")
    return diffs


# ---------------------------------------------------------------------------
# running a transfer with one binary

def run_xfer(binary, workdir, opts, src_args, dest, cwd=None, pre=None):
    """Run `binary opts src_args dest` (cwd default=workdir). Returns
    (rc, stderr, literal_data, itemize)."""
    cwd = cwd or workdir
    argv = [binary, "--stats", "-i", *opts, *src_args, dest]
    return _parse_out(sh(argv, cwd=cwd))


def _parse_out(r):
    literal = None
    for ln in r.stdout.splitlines():
        if ln.startswith("Literal data:"):
            literal = ln.split(":", 1)[1].strip()
    itemize = "\n".join(sorted(
        ln for ln in r.stdout.splitlines()
        if len(ln) > 11 and ln[1] in "fdLDS" and ln[0] in "<>ch.*"
        and ln[11:].strip() != "./"))  # bench-dependent top-dir time line
    return r.returncode, (r.stderr or "").strip(), literal, itemize, (r.stdout or "")


_NOISE = re.compile(r'bytes/sec|speedup is|^sent .*received |^total size is|'
                    r'^Number of |^Total |^Literal data:|^Matched data:|'
                    r'^File list |^total:|^created |^deleting ')


def _norm_out(text, wd, dest):
    """Normalised stdout for A/B compare: canonicalise the workdir/dest paths and
    drop bench-variant stats lines (keeps itemize/listing/warning lines)."""
    text = text.replace(str(dest), "DEST").replace(str(wd), "WD")
    return "\n".join(ln for ln in text.splitlines() if ln and not _NOISE.search(ln))


def _norm_err(text, wd, dest):
    """Normalised stderr TEXT for A/B compare: canonicalise paths, strip the
    version-dependent role tag ([sender=3.4.x], [client=VERSION]) and at-FILE(LINE)
    source location (line numbers shift between versions) so only the MESSAGE is
    compared."""
    text = text.replace(str(dest), "DEST").replace(str(wd), "WD")
    text = re.sub(r'\[(?:sender|receiver|generator|client|server'
                  r'|Sender|Receiver|Generator|Client|Server)=[^\]]*\]',
                  '[ROLE]', text)
    text = re.sub(r' at [\w./-]+\(\d+\)', ' at LOC', text)
    return text.strip()


def _wait_port(port, timeout=10.0):
    import socket as _sock
    end = time.time() + timeout
    while time.time() < end:
        try:
            _sock.create_connection(("127.0.0.1", port), 0.3).close()
            return True
        except OSError:
            time.sleep(0.05)
    return False


def run_daemon_xfer(binary, wd, module_path, opts, src_args, port, chroot="no"):
    """Push src_args into a [m] module served by `binary --daemon` over a PRIVATE
    STDIO PIPE (RSYNC_CONNECT_PROG) -- no TCP port, so no port-bind/startup race
    (the old TCP path was nondeterministic under load).  `port` only uniquifies
    the per-invocation config filename.  Same (rc, err, lit, item) as run_xfer."""
    conf = Path(wd) / f"rsyncd_{port}.conf"
    Path(module_path).mkdir(parents=True, exist_ok=True)
    conf.write_text(
        f"use chroot = {chroot}\n"
        f"[m]\n  path = {module_path}\n  read only = no\n"
        f"  hosts allow = 127.0.0.1\n")
    env = {**os.environ,
           "RSYNC_CONNECT_PROG": f"{binary} --config={conf} --daemon"}
    argv = [binary, "--stats", "-i", *opts, *src_args, "rsync://localhost/m/"]
    return _parse_out(sh(argv, cwd=str(wd), env=env))


def run_daemon_pull(binary, wd, served, opts, localdest, port, chroot="no"):
    """PULL from a read-only [m] module (the daemon SENDER side) over a private
    stdio pipe into localdest. `served` is the served directory."""
    conf = Path(wd) / f"rsyncd_{port}.conf"
    Path(served).mkdir(parents=True, exist_ok=True)
    Path(localdest).mkdir(parents=True, exist_ok=True)
    conf.write_text(
        f"use chroot = {chroot}\n"
        f"[m]\n  path = {served}\n  read only = yes\n"
        f"  hosts allow = 127.0.0.1\n")
    env = {**os.environ,
           "RSYNC_CONNECT_PROG": f"{binary} --config={conf} --daemon"}
    argv = [binary, "--stats", "-i", *opts, "rsync://localhost/m/",
            str(localdest) + "/"]
    return _parse_out(sh(argv, cwd=str(wd), env=env))


# sibling helper scripts live in support/ (abdiff.py itself lives in testsuite/)
_SUPPORT = Path(__file__).resolve().parent.parent / "support"
_LSH = str(_SUPPORT / "lsh.sh")


def run_ssh_xfer(binary, wd, opts, src_args, dest):
    """PUSH over a remote-shell split via support/lsh.sh (host 'lh' = no chdir);
    the remote side runs the same binary via --rsync-path. Real client+server
    processes + protocol, unlike a both-paths-local copy."""
    argv = [binary, "--stats", "-i", "-e", f"sh {_LSH}",
            f"--rsync-path={binary}", *opts, *src_args, f"lh:{dest}/"]
    return _parse_out(sh(argv, cwd=wd))


# --- rrsync lane: route the remote side through the restricted rrsync wrapper --
_RRSH = str(_SUPPORT / "rrsh.sh")
_RRSYNC_SRC = _SUPPORT / "rrsync"


def _patch_rrsync(binary, rrsync_src, wd):
    """A copy of `rrsync_src` (the version's rrsync wrapper) whose RSYNC points at
    `binary`, so the rrsync-launched server is the binary under test. rrsync is
    SHIPPED PER VERSION, so A and B use their OWN rrsync (the regressions live in
    the script, not just the binary) -- keyed per (binary, src) to keep them
    distinct in a shared workdir."""
    key = abs(hash((binary, str(rrsync_src)))) % 1000000
    dst = Path(wd) / f"rrsync-{key}"
    if not dst.exists():
        txt = Path(rrsync_src).read_text()
        txt = re.sub(r"^RSYNC = '[^']*'", f"RSYNC = {binary!r}", txt, count=1,
                     flags=re.M)
        dst.write_text(txt)
        dst.chmod(0o755)
    return dst


def run_rrsync_push(binary, rrsync_src, wd, opts, src_args, dest):
    """PUSH through `rrsync <restricted>` (the dest's parent is the restricted
    root; the client writes into the <dest-name>/ subdir, so rrsync's subdir
    restrictions are exercised). Exercises rrsync option/path validation."""
    dest = Path(dest)
    rr = _patch_rrsync(binary, rrsync_src, wd)
    dest.parent.mkdir(parents=True, exist_ok=True)
    argv = [binary, "--stats", "-i", "-e", f"sh {_RRSH} {rr} {dest.parent}",
            *opts, *src_args, f"lh:{dest.name}/"]
    return _parse_out(sh(argv, cwd=str(wd)))


def run_rrsync_pull(binary, rrsync_src, wd, served, opts, localdest):
    """PULL through `rrsync <restricted>` (rrsync's --sender side): the served
    dir's parent is the restricted root, the client reads the <served-name>/
    subdir into localdest."""
    served = Path(served)
    rr = _patch_rrsync(binary, rrsync_src, wd)
    Path(localdest).mkdir(parents=True, exist_ok=True)
    argv = [binary, "--stats", "-i", "-e", f"sh {_RRSH} {rr} {served.parent}",
            *opts, f"lh:{served.name}/", str(localdest) + "/"]
    return _parse_out(sh(argv, cwd=str(wd)))


# --- real-TCP-daemon lane: a genuine `rsync --daemon` on a bound port ----------
_port_lock = threading.Lock()
_next_port = [40000]


def _alloc_port():
    """A free localhost TCP port, bind-probed under a lock so concurrent workers
    don't collide (the real-daemon path needs a real port, unlike the stdio one)."""
    import socket as _s
    with _port_lock:
        for _ in range(4000):
            p = _next_port[0]
            _next_port[0] = 40000 if p >= 60000 else p + 1
            s = _s.socket(_s.AF_INET, _s.SOCK_STREAM)
            try:
                s.setsockopt(_s.SOL_SOCKET, _s.SO_REUSEADDR, 1)
                s.bind(("127.0.0.1", p))
                return p
            except OSError:
                continue
            finally:
                s.close()
    return _next_port[0]


def _tcp_daemon(binary, wd, module_path, opts, src_args, localdest=None,
                pull=False, chroot="no", auth=False):
    """Start a real `binary --daemon` on a bound TCP port and run the client over
    rsync://127.0.0.1:PORT/m/ -- exercises the genuine socket path / greeting /
    handshake (and, with auth, the challenge-response) that the stdio-pipe daemon
    lane bypasses."""
    port = _alloc_port()
    conf = Path(wd) / f"tcpd_{port}.conf"
    Path(module_path).mkdir(parents=True, exist_ok=True)
    authlines = ""
    env = dict(os.environ)
    if auth:
        sp = Path(wd) / f"tcpd_{port}.secrets"
        sp.write_text("abuser:abpass\n")
        sp.chmod(0o600)
        authlines = f"  auth users = abuser\n  secrets file = {sp}\n"
        env["RSYNC_PASSWORD"] = "abpass"
    conf.write_text(
        f"use chroot = {chroot}\nport = {port}\n"
        f"log file = {wd}/tcpd_{port}.log\npid file = {wd}/tcpd_{port}.pid\n"
        f"[m]\n  path = {module_path}\n  read only = {'yes' if pull else 'no'}\n"
        f"  hosts allow = 127.0.0.1\n{authlines}")
    proc = subprocess.Popen(
        [binary, "--daemon", "--no-detach", f"--config={conf}",
         f"--port={port}", "--address=127.0.0.1"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True)
    try:
        if not _wait_port(port):
            return (99, "tcp daemon failed to start", None, "", "")
        user = "abuser@" if auth else ""
        url = f"rsync://{user}127.0.0.1:{port}/m/"
        if pull:
            Path(localdest).mkdir(parents=True, exist_ok=True)
            argv = [binary, "--stats", "-i", *opts, url, str(localdest) + "/"]
        else:
            argv = [binary, "--stats", "-i", *opts, *src_args, url]
        return _parse_out(sh(argv, cwd=str(wd), env=env))
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


# ---------------------------------------------------------------------------
# fixtures

def _write(p: Path, data: bytes):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)


def build_kitchen(src: Path):
    """A benign 'kitchen-sink' tree: regular/empty/large/sparse files, nested
    and empty dirs, in-tree + dangling symlinks, a dir-symlink, a hardlink pair,
    odd modes, and a user.* xattr. No attacker paths."""
    src.mkdir(parents=True, exist_ok=True)
    _write(src / "empty", b"")
    _write(src / "small.txt", b"hello world\n")
    _write(src / "data.bin", bytes((i * 7) & 0xFF for i in range(200000)))
    _write(src / "dir/a.txt", b"a" * 100)
    _write(src / "dir/sub/b.txt", b"b" * 100)
    (src / "emptydir").mkdir(exist_ok=True)
    # sparse file: hole + data
    with open(src / "sparse.bin", "wb") as f:
        f.seek(1 << 20)
        f.write(b"END")
    # hardlink pair
    _write(src / "hl_a", b"hardlinked\n")
    try:
        os.link(src / "hl_a", src / "hl_b")
    except OSError:
        pass
    # symlinks
    os.symlink("small.txt", src / "rel_link")          # in-tree relative
    os.symlink("dir", src / "dir_link")                # dir symlink
    os.symlink("nonexistent", src / "dangling")        # dangling
    # odd modes
    os.chmod(src / "small.txt", 0o4755)                # setuid
    os.chmod(src / "dir", 0o2775)                       # setgid dir
    # xattr (best effort)
    try:
        os.setxattr(src / "data.bin", "user.abtest", b"v1")
    except OSError:
        pass


def build_relfile(src: Path):
    """Minimal nested file for path-shape tests."""
    _write(src / "sub/deep/file", b"relative content\n")
    os.symlink("file", src / "sub/deep/link")


# ---------------------------------------------------------------------------
# scenarios

class Scenario:
    def __init__(self, name, setup, opts, src_args, dest="destX/",
                 cwd_is_workdir=True, pre_dest=None, abspath=False,
                 dest_prep=None, snap_dest=None, dest_arg=None, daemon=None,
                 ssh=False):
        self.name = name
        self.setup = setup            # fn(src_dir)
        self.opts = opts              # list[str]
        self.src_args = src_args      # fn(workdir)->list[str] OR list[str]
        self.dest = dest
        self.cwd_is_workdir = cwd_is_workdir
        self.pre_dest = pre_dest      # fn(dest_dir) to pre-populate (delete/update)
        self.abspath = abspath
        # dest_prep(dest_path): create the dest specially (e.g. as a symlink to a
        # real dir) instead of letting rsync create it. snap_dest(dest_path)->Path
        # picks what to snapshot (e.g. the symlink's real target).
        self.dest_prep = dest_prep
        self.snap_dest = snap_dest
        # dest_arg(dest_base)->str: the actual rsync destination argument (e.g.
        # write THROUGH an in-tree dir-symlink: dest_base/link/). Default is
        # dest_base + "/".
        self.dest_arg = dest_arg
        # daemon: None for a local transfer, or {"chroot": "no"|"yes"} to PUSH
        # src_args into a [m] daemon module whose path is the dest dir.
        self.daemon = daemon
        # ssh: True to PUSH over a remote-shell split (support/lsh.sh, host "lh")
        # -- separate client+server processes, real protocol + arg passing.
        self.ssh = ssh
        # rrsync: None, or {"pull": bool} to route through the restricted rrsync
        # wrapper (support/rrsync) as an sshd forced-command would -- exercises
        # rrsync's own option/path validation. ssh/daemon-style transport.
        self.rrsync = None


def _liftable(scn):
    """A benign push-into-dest scenario whose transport can be swapped for free:
    no dest_prep/dest_arg (those need local dest-path semantics), not already a
    daemon/ssh scenario, and a list src_args that ends by pushing src/ -> dest."""
    return (not scn.dest_prep and not scn.dest_arg and not scn.abspath
            and scn.daemon is None and not scn.ssh and scn.rrsync is None
            and isinstance(scn.src_args, list) and scn.src_args
            and scn.src_args[-1] in ("src/", "src"))


def _clone_transport(scn, mode):
    import copy
    c = copy.copy(scn)
    c.name = f"{scn.name}@{mode}"
    if mode == "ssh":
        c.ssh = True
    elif mode == "daemon":
        c.daemon = {"chroot": "no"}
    return c


def lift_transports(scns, modes=("ssh", "daemon")):
    """Make transport an ORTHOGONAL axis: keep each local scenario and, for the
    liftable ones, also run it over ssh and a daemon module. This is where the
    daemon/ssh-only regression family hides -- a feature broken only over the
    wire is invisible to a local-only sweep."""
    out = []
    for scn in scns:
        out.append(scn)
        if _liftable(scn):
            out += [_clone_transport(scn, m) for m in modes]
    return out


def options_sweep():
    """-a plus one option at a time, over the kitchen-sink, relative trailing
    slash source -> dest. The bread-and-butter single-option regression check."""
    base = ["-a"]
    variants = [
        ["-a"], ["-aH"], ["-aHS"], ["-a", "--sparse"], ["-a", "--inplace"],
        ["-a", "-A"], ["-a", "-X"], ["-a", "-AX"], ["-a", "-U"], ["-a", "-N"],
        ["-a", "-l"], ["-a", "-L"], ["-a", "-k"], ["-a", "-K"],
        ["-a", "--copy-unsafe-links"], ["-a", "--safe-links"],
        ["-a", "--munge-links"], ["-a", "-z"], ["-a", "--compress-choice=zstd"],
        ["-a", "--compress-choice=zlib"], ["-a", "-c"],
        ["-a", "--checksum-choice=md5"], ["-a", "-W"], ["-a", "--no-whole-file"],
        ["-a", "-O"], ["-a", "-J"], ["-a", "--numeric-ids"], ["-a", "-E"],
        ["-a", "--no-inc-recursive"], ["-a", "--fake-super"],
        ["-a", "--chmod=u+rwx"], ["-rlptD"], ["-rtz"],
        ["-a", "-B", "1024"], ["-a", "--max-size=1000"], ["-a", "--min-size=50"],
        ["-a", "--exclude=*.bin"], ["-a", "-C"], ["-a", "--prune-empty-dirs"],
    ]
    scns = []
    for v in variants:
        nm = "opt:" + "_".join(x.lstrip("-") for x in v if x != "-a") or "opt:a"
        scns.append(Scenario("opt:" + "+".join(v), build_kitchen, v,
                             ["src/"], "dest/"))
    return scns


def pathshape_sweep():
    """The --relative class: same content under many source-path shapes."""
    scns = []

    def absfile(wd):
        return [str(Path(wd) / "src/sub/deep/file")]

    scns += [
        Scenario("path:rel-dir-slash", build_relfile, ["-a"], ["src/"], "dest/"),
        Scenario("path:rel-dir-noslash", build_relfile, ["-a"], ["src"], "dest/"),
        Scenario("path:rel-file", build_relfile, ["-a"], ["src/sub/deep/file"], "dest/"),
        Scenario("path:abs-file", build_relfile, ["-a"], absfile, "dest/"),
        Scenario("path:abs-dir", build_relfile, ["-a"],
                 lambda wd: [str(Path(wd) / "src") + "/"], "dest/"),
        Scenario("path:R-rel-file", build_relfile, ["-aR"], ["src/sub/deep/file"], "dest/"),
        Scenario("path:R-abs-file", build_relfile, ["-aR"], absfile, "dest/"),
        Scenario("path:R-dot", build_relfile, ["-aR"], ["./src/sub/deep/file"], "dest/"),
        Scenario("path:R-rel-dir", build_relfile, ["-aR"], ["src/sub/"], "dest/"),
        Scenario("path:R-noimplied", build_relfile, ["-aR", "--no-implied-dirs"],
                 ["src/sub/deep/file"], "dest/"),
        Scenario("path:multi-src", build_relfile, ["-a"],
                 ["src/sub/deep/file", "src/sub/deep/link"], "dest/"),
        Scenario("path:link-as-src", build_relfile, ["-a"], ["src/sub/deep/link"], "dest/"),
        Scenario("path:L-link-as-src", build_relfile, ["-aL"], ["src/sub/deep/link"], "dest/"),
    ]
    return scns


T_OLD = 1000000000   # fixed timestamps so pre-state is identical for A and B
T_NEW = 1700000000


def _ut(p, t=T_NEW):
    os.utime(p, (t, t))


def build_recvtree(src: Path):
    """Small, interpretable source tree for receiver/stateful scenarios."""
    _write(src / "file1.txt", b"NEW content line\n" * 3)
    _write(src / "dir/file2.txt", b"data2\n")
    _write(src / "big.bin", bytes((i * 3) & 0xFF for i in range(60000)))
    os.symlink("file1.txt", src / "slink")
    _write(src / "hl1", b"hard\n")
    try:
        os.link(src / "hl1", src / "hl2")
    except OSError:
        pass
    for f in ("file1.txt", "dir/file2.txt", "big.bin", "hl1", "hl2"):
        _ut(src / f)
    _ut(src / "dir")
    _ut(src)


def setup_with_basis(src: Path):
    """src plus an identical sibling basis/ (for --link-dest/--copy-dest/etc.)."""
    build_recvtree(src)
    basis = src.parent / "basis"
    build_recvtree(basis)


def stale_dest(dest: Path):
    """Pre-populate a dest as an OLDER state: file1 differs (older mtime),
    big.bin differs (older), an extra obsolete file, file2 already current."""
    _write(dest / "file1.txt", b"OLD content\n")
    _ut(dest / "file1.txt", T_OLD)
    _write(dest / "dir/file2.txt", b"data2\n")
    _ut(dest / "dir/file2.txt")
    _ut(dest / "dir")
    _write(dest / "big.bin", bytes((i * 5) & 0xFF for i in range(60000)))
    _ut(dest / "big.bin", T_OLD)
    _write(dest / "obsolete.txt", b"remove me\n")
    _ut(dest / "obsolete.txt", T_OLD)


# ===========================================================================
# Domain-knowledge-driven scenario generation.
# "Edges of interest": equivalence-class boundary representatives, not volume
# (empty-dir vs 1-file matters; 10 vs 11 doesn't; mode 0 vs 0400 vs 0200 matters;
# 100 files of one mode don't). Each option is modelled by its precondition (the
# dest/src state that makes it active) and, for options taking a dir, whether the
# aux location sits INSIDE or OUTSIDE the tree.
# ===========================================================================

# interesting permission edges (no-perm / read / write / exec / special bits)
MODES = [0o000, 0o400, 0o200, 0o644, 0o755, 0o4755, 0o2755, 0o1777]
# size edges around rsync's block boundary (BLOCK_SIZE 700): empty/1B/under/at/over/multi
SIZES = [0, 1, 699, 700, 701, 100003]


def _mk_reg(p, n, mode=0o644, t=T_NEW, fill=7):
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "wb") as f:
        f.write(bytes((i * fill) & 0xFF for i in range(n)))
    os.chmod(p, mode)
    _ut(p, t)


def _mk_sparse(p, hole=1 << 20, tail=b"end"):
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "wb") as f:
        f.seek(hole)
        f.write(tail)
    _ut(p)


def _mk_fifo(p):
    p.parent.mkdir(parents=True, exist_ok=True)
    os.mkfifo(p)


def _mk_sock(p):
    import socket as _s
    p.parent.mkdir(parents=True, exist_ok=True)
    s = _s.socket(_s.AF_UNIX)
    try:
        s.bind(str(p))
    finally:
        s.close()


def mode_sweep():
    """One representative file per interesting permission edge (+ a setgid/sticky
    dir), plain -a and -a --chmod. Boundary values, not 100 files of one mode."""
    s = []
    for m in MODES:
        def setup(src, m=m):
            _mk_reg(src / "f", 64, mode=m)
            os.mkdir(src / "d")
            os.chmod(src / "d", 0o2755 if m == 0o2755 else
                     (0o1777 if m == 0o1777 else 0o755))
            _ut(src / "d")
        s.append(Scenario(f"mode:{m:04o}", setup, ["-a"], ["src/"], "dest/"))
    s.append(Scenario("mode:chmod-Dg-Fo", lambda src: _mk_reg(src / "f", 8),
                      ["-a", "--chmod=D2755,F644"], ["src/"], "dest/"))
    return s


def size_sweep():
    """One file per size edge around the block boundary, plain and --inplace
    (delta path), -c (whole-file checksum), -S on a sparse file."""
    s = []
    for n in SIZES:
        s.append(Scenario(f"size:{n}", lambda src, n=n: _mk_reg(src / "f", n),
                          ["-a"], ["src/"], "dest/"))
        s.append(Scenario(f"size:{n}+inplace",
                          lambda src, n=n: _mk_reg(src / "f", n),
                          ["-a", "--inplace", "--no-whole-file"], ["src/"], "dest/"))
    s.append(Scenario("size:sparse", lambda src: _mk_sparse(src / "sp.bin"),
                      ["-aS"], ["src/"], "dest/"))
    s.append(Scenario("size:sparse-inplace", lambda src: _mk_sparse(src / "sp.bin"),
                      ["-aS", "--inplace"], ["src/"], "dest/"))
    return s


def filetype_sweep():
    """One representative per file type/symlink shape, each with the option(s)
    that actually exercise it. Specials/devices are in priv_sweep (root)."""
    def base(src):
        _mk_reg(src / "anchor", 16)

    def f_emptydir(src):
        base(src)
        os.makedirs(src / "empty")
        _ut(src / "empty")

    def f_intree(src):
        base(src)
        _mk_reg(src / "real", 20)
        os.symlink("real", src / "lnk")          # in-tree relative

    def f_dirlink(src):
        base(src)
        _mk_reg(src / "rd/inner", 20)
        os.symlink("rd", src / "dl")             # symlink to a dir

    def f_abslink(src):
        base(src)
        os.symlink("/etc/hostname", src / "abs")  # out-of-tree absolute

    def f_dangling(src):
        base(src)
        os.symlink("nonexistent", src / "dead")

    def f_chain(src):
        base(src)
        _mk_reg(src / "real", 20)
        os.symlink("real", src / "l1")
        os.symlink("l1", src / "l2")             # symlink chain

    def f_hardlinks(src):
        _mk_reg(src / "a", 40)
        os.link(src / "a", src / "b")
        os.link(src / "a", src / "c")
        base(src)

    def f_fifo(src):
        base(src)
        _mk_fifo(src / "pipe")

    def f_sock(src):
        base(src)
        _mk_sock(src / "sock")

    return [
        Scenario("ft:emptydir", f_emptydir, ["-a"], ["src/"], "dest/"),
        Scenario("ft:intree-l", f_intree, ["-a"], ["src/"], "dest/"),
        Scenario("ft:intree-L", f_intree, ["-aL"], ["src/"], "dest/"),
        Scenario("ft:dirlink-l", f_dirlink, ["-a"], ["src/"], "dest/"),
        Scenario("ft:dirlink-k", f_dirlink, ["-ak"], ["src/"], "dest/"),
        Scenario("ft:dirlink-L", f_dirlink, ["-aL"], ["src/"], "dest/"),
        Scenario("ft:abslink-l", f_abslink, ["-a"], ["src/"], "dest/"),
        Scenario("ft:abslink-L", f_abslink, ["-aL"], ["src/"], "dest/"),
        Scenario("ft:abslink-safe", f_abslink, ["-a", "--safe-links"], ["src/"], "dest/"),
        Scenario("ft:abslink-munge", f_abslink, ["-a", "--munge-links"], ["src/"], "dest/"),
        Scenario("ft:dangling", f_dangling, ["-a"], ["src/"], "dest/"),
        Scenario("ft:chain-l", f_chain, ["-a"], ["src/"], "dest/"),
        Scenario("ft:chain-L", f_chain, ["-aL"], ["src/"], "dest/"),
        Scenario("ft:hardlinks", f_hardlinks, ["-aH"], ["src/"], "dest/"),
        Scenario("ft:fifo", f_fifo, ["-a"], ["src/"], "dest/"),
        Scenario("ft:sock", f_sock, ["-a"], ["src/"], "dest/"),
    ]


# --- preconditions: dest/src state that makes an option actually ACTIVE -------
def _pc_tree(src):
    _mk_reg(src / "f", 100)
    _mk_reg(src / "dir/g", 50)
    _ut(src / "dir")
    _ut(src)


def _setup_samemeta(src):
    _mk_reg(src / "f", 100, t=T_NEW, fill=7)
    _mk_reg(src / "keep", 20, t=T_NEW, fill=7)


def _pre_samemeta(dest):                 # same size+mtime, DIFFERENT content
    _mk_reg(dest / "f", 100, t=T_NEW, fill=200)
    _mk_reg(dest / "keep", 20, t=T_NEW, fill=200)


def _setup_older(src):
    _mk_reg(src / "f", 100, t=T_OLD, fill=7)


def _pre_newer(dest):                    # dest newer + different (for -u)
    _mk_reg(dest / "f", 60, t=T_NEW, fill=200)


def _setup_mixed(src):                   # an existing file + a new file
    _mk_reg(src / "exist", 30, t=T_NEW, fill=7)
    _mk_reg(src / "newfile", 30, t=T_NEW, fill=7)


def _pre_existing(dest):                 # only "exist" present (older, different)
    _mk_reg(dest / "exist", 99, t=T_OLD, fill=200)


def _setup_sizes(src):
    _mk_reg(src / "small", 50)
    _mk_reg(src / "big", 5000)


def _setup_emptydirs(src):
    os.makedirs(src / "empty/sub")
    _mk_reg(src / "keep/f", 10)
    _ut(src / "keep")
    _ut(src / "empty/sub")
    _ut(src / "empty")


def _setup_append(src):
    _mk_reg(src / "f", 200, t=T_NEW, fill=7)


def _pre_append(dest):                   # dest is a shorter prefix (for --append)
    with open(dest / "f", "wb") as fh:
        fh.write(bytes((i * 7) & 0xFF for i in range(80)))
    _ut(dest / "f", T_OLD)


def _setup_kdest(src):
    _mk_reg(src / "dir/f1", 10)
    _mk_reg(src / "dir/f2", 10)
    _mk_reg(src / "top", 5)


def _prep_kdest(dest):                   # dest has realdir + dir->realdir symlink
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "realdir").mkdir(exist_ok=True)
    if not (dest / "dir").is_symlink():
        os.symlink("realdir", dest / "dir")


def selection_sweep():
    """File-selection / timestamp / size options at the boundary that makes the
    comparison non-trivial (same size+mtime but different content, newer dest,
    existing-vs-new, size limits)."""
    plain = "src/"
    s = [
        Scenario("sel:checksum", _setup_samemeta, ["-a", "-c"], [plain], "dest/", pre_dest=_pre_samemeta),
        Scenario("sel:ignore-times", _setup_samemeta, ["-a", "-I"], [plain], "dest/", pre_dest=_pre_samemeta),
        Scenario("sel:size-only", _setup_samemeta, ["-a", "--size-only"], [plain], "dest/", pre_dest=_pre_samemeta),
        Scenario("sel:quickcheck", _setup_samemeta, ["-a"], [plain], "dest/", pre_dest=_pre_samemeta),
        Scenario("sel:update", _setup_older, ["-a", "-u"], [plain], "dest/", pre_dest=_pre_newer),
        Scenario("sel:modify-window", _setup_samemeta, ["-a", "--modify-window=2"], [plain], "dest/", pre_dest=_pre_samemeta),
        Scenario("sel:existing", _setup_mixed, ["-a", "--existing"], [plain], "dest/", pre_dest=_pre_existing),
        Scenario("sel:ignore-existing", _setup_mixed, ["-a", "--ignore-existing"], [plain], "dest/", pre_dest=_pre_existing),
        Scenario("sel:max-size", _setup_sizes, ["-a", "--max-size=1000"], [plain], "dest/"),
        Scenario("sel:min-size", _setup_sizes, ["-a", "--min-size=1000"], [plain], "dest/"),
        Scenario("sel:times-only", _pc_tree, ["-rlpt"], [plain], "dest/"),
        Scenario("sel:atimes", _pc_tree, ["-a", "--atimes"], [plain], "dest/"),
        Scenario("sel:crtimes", _pc_tree, ["-a", "--crtimes"], [plain], "dest/"),
        Scenario("sel:open-noatime", _pc_tree, ["-a", "--open-noatime"], [plain], "dest/"),
    ]
    return lift_transports(s)


def behavior_sweep():
    """Behaviour options at their active preconditions (overwrite/backup/delete/
    inplace/append/keep-dirlinks-dest/prune-empty/mkpath/dirs)."""
    return [
        Scenario("beh:backup", build_recvtree, ["-ab"], ["src/"], "dest/", pre_dest=stale_dest),
        Scenario("beh:delete", build_recvtree, ["-a", "--delete"], ["src/"], "dest/", pre_dest=stale_dest),
        Scenario("beh:delete-before", build_recvtree, ["-a", "--delete-before"], ["src/"], "dest/", pre_dest=stale_dest),
        Scenario("beh:delete-after", build_recvtree, ["-a", "--delete-after"], ["src/"], "dest/", pre_dest=stale_dest),
        Scenario("beh:delete-delay", build_recvtree, ["-a", "--delete-delay"], ["src/"], "dest/", pre_dest=stale_dest),
        Scenario("beh:delete-excluded", build_recvtree,
                 ["-a", "--delete", "--delete-excluded", "--exclude=obsolete.txt"],
                 ["src/"], "dest/", pre_dest=stale_dest),
        Scenario("beh:prune-empty", _setup_emptydirs, ["-a", "-m"], ["src/"], "dest/"),
        Scenario("beh:inplace", build_recvtree, ["-a", "--inplace"], ["src/"], "dest/", pre_dest=stale_dest),
        Scenario("beh:append", _setup_append, ["-a", "--append"], ["src/"], "dest/", pre_dest=_pre_append),
        Scenario("beh:numeric-ids", build_recvtree, ["-a", "--numeric-ids"], ["src/"], "dest/"),
        Scenario("beh:delay-updates", build_recvtree, ["-a", "--delay-updates"], ["src/"], "dest/", pre_dest=stale_dest),
        Scenario("beh:dirs", _pc_tree, ["-dlpt"], ["src/"], "dest/"),
        Scenario("beh:mkpath", _pc_tree, ["-a", "--mkpath"], ["src/"], "dest/",
                 dest_arg=lambda d: str(d) + "/made/sub/"),
        Scenario("beh:keep-dirlinks-dest", _setup_kdest, ["-aK"], ["src/"], "dest/",
                 dest_prep=_prep_kdest, snap_dest=lambda d: d / "realdir"),
    ]


def _auxp(dest, where, name):
    """Aux path INSIDE the dest tree, or OUTSIDE it (sibling under the workdir)."""
    dest = Path(dest)
    return str((dest / name) if where == "inside" else (dest.parent / f"aux_{name}"))


def placement_sweep():
    """Options that take a DIR/path, each with the aux location INSIDE vs OUTSIDE
    the destination tree -- the edge most likely to expose confinement/escape
    regressions.  backup/temp/partial-dir + link/compare/copy-dest."""
    s = []
    for where in ("inside", "outside"):
        s.append(Scenario(f"place:backup-dir-{where}", build_recvtree,
                 (lambda wd, dest, w=where: ["-ab", f"--backup-dir={_auxp(dest, w, 'bak')}"]),
                 ["src/"], "dest/", pre_dest=stale_dest))
        # temp-dir must exist -> pre-create dest (empty) + the temp dir
        def _prep_temp(dest, w=where):
            dest.mkdir(parents=True, exist_ok=True)
            os.makedirs(_auxp(dest, w, "tmp"), exist_ok=True)
        s.append(Scenario(f"place:temp-dir-{where}", build_recvtree,
                 (lambda wd, dest, w=where: ["-a", f"--temp-dir={_auxp(dest, w, 'tmp')}"]),
                 ["src/"], "dest/", dest_prep=_prep_temp))
        s.append(Scenario(f"place:partial-dir-{where}", build_recvtree,
                 (lambda wd, dest, w=where: ["-a", "--partial", f"--partial-dir={_auxp(dest, w, 'part')}"]),
                 ["src/"], "dest/"))
    # alt-dest basis (a prior identical copy at wd/basis): absolute vs relative path
    for opt in ("link-dest", "compare-dest", "copy-dest"):
        s.append(Scenario(f"place:{opt}-abs", setup_with_basis,
                 (lambda wd, dest, o=opt: ["-a", f"--{o}={wd}/basis"]), ["src/"], "dest/"))
        s.append(Scenario(f"place:{opt}-rel", setup_with_basis,
                 (lambda wd, dest, o=opt: ["-a", f"--{o}=../basis"]), ["src/"], "dest/"))
    return lift_transports(s)


def wire_sweep():
    """Protocol / wire / algorithm options (checksum & compress choice, old/
    secluded args, iconv, odd block sizes)."""
    bt = build_recvtree
    return [
        Scenario("wire:cc-md5", bt, ["-a", "--checksum-choice=md5"], ["src/"], "dest/"),
        Scenario("wire:cc-md4", bt, ["-a", "--checksum-choice=md4"], ["src/"], "dest/"),
        Scenario("wire:cc-xxh64", bt, ["-a", "--checksum-choice=xxh64"], ["src/"], "dest/"),
        Scenario("wire:zc-zstd", bt, ["-a", "-z", "--compress-choice=zstd"], ["src/"], "dest/"),
        Scenario("wire:zc-zlib", bt, ["-a", "-z", "--compress-choice=zlib"], ["src/"], "dest/"),
        Scenario("wire:zc-zlibx", bt, ["-a", "-z", "--compress-choice=zlibx"], ["src/"], "dest/"),
        Scenario("wire:old-args", bt, ["-a", "--old-args"], ["src/"], "dest/"),
        Scenario("wire:secluded-args", bt, ["-a", "-s"], ["src/"], "dest/"),
        Scenario("wire:iconv", bt, ["-a", "--iconv=utf8,latin1"], ["src/"], "dest/"),
        Scenario("wire:block-1024", bt, ["-a", "-B", "1024"], ["src/"], "dest/"),
        Scenario("wire:block-999", bt, ["-a", "-B", "999"], ["src/"], "dest/"),
    ]


# module-level file-type fixtures (also used by the pairwise sweep)
def _ft_intree(src):
    _mk_reg(src / "real", 20)
    os.symlink("real", src / "lnk")
    _mk_reg(src / "anchor", 10)


def _ft_dirlink(src):
    _mk_reg(src / "rd/inner", 20)
    os.symlink("rd", src / "dl")
    _mk_reg(src / "anchor", 10)
    _ut(src / "rd")


def _ft_hardlinks(src):
    _mk_reg(src / "a", 40)
    os.link(src / "a", src / "b")
    os.link(src / "a", src / "c")
    _mk_reg(src / "anchor", 10)


def _ft_sparse(src):
    _mk_sparse(src / "sp.bin")
    _mk_reg(src / "anchor", 10)


def pairwise_sweep():
    """Guided pairwise: curated feature interactions (domain knowledge about where
    two options collide), plus an auto option x file-type covering set."""
    s = [
        Scenario("pair:delete+dirlink-dest", _setup_kdest, ["-aK", "--delete"],
                 ["src/"], "dest/", dest_prep=_prep_kdest, snap_dest=lambda d: d / "realdir"),
        Scenario("pair:backup-inside+delete", build_recvtree,
                 (lambda wd, dest: ["-ab", "--delete", f"--backup-dir={_auxp(dest, 'inside', 'bak')}"]),
                 ["src/"], "dest/", pre_dest=stale_dest),
        Scenario("pair:inplace+sparse", _ft_sparse,
                 ["-aS", "--inplace", "--no-whole-file"], ["src/"], "dest/"),
        Scenario("pair:link-dest+hardlinks", setup_with_basis,
                 (lambda wd, dest: ["-aH", f"--link-dest={wd}/basis"]), ["src/"], "dest/"),
        Scenario("pair:copy-links+dirlink", _ft_dirlink, ["-aL"], ["src/"], "dest/"),
        Scenario("pair:keepdirlinks+delete-excluded", _setup_kdest,
                 ["-aK", "--delete", "--delete-excluded", "--exclude=top"],
                 ["src/"], "dest/", dest_prep=_prep_kdest, snap_dest=lambda d: d / "realdir"),
        Scenario("pair:sparse+whole-file", _ft_sparse, ["-aS", "-W"], ["src/"], "dest/"),
        Scenario("pair:partial-inside+delete", build_recvtree,
                 (lambda wd, dest: ["-a", "--delete", "--partial",
                                    f"--partial-dir={_auxp(dest, 'inside', 'part')}"]),
                 ["src/"], "dest/", pre_dest=stale_dest),
    ]
    # auto option x file-type covering set (each option relevant to several types)
    fts = [("intree", _ft_intree), ("dirlink", _ft_dirlink),
           ("hardlinks", _ft_hardlinks), ("sparse", _ft_sparse)]
    opts = ["-c", "-z", "-b", "--inplace", "-H", "-L", "-k", "--checksum-choice=md5"]
    for ftn, ftfn in fts:
        for o in opts:
            tag = o.lstrip("-").split("=")[0]
            s.append(Scenario(f"pair:{tag}x{ftn}", ftfn, ["-a", o], ["src/"], "dest/"))
    return s


def recv_sweep():
    """Receiver/generator + stateful scenarios: existing-dest update/delete,
    backup, dest-variants (link/compare/copy-dest), inplace, temp-dir. These
    exercise the receiver's existing-dest / basis / temp-dir paths that the
    single-transfer-into-empty-dest sweeps don't reach."""
    s = []
    s += [
        Scenario("recv:update", build_recvtree, ["-a"], ["src/"], "dest/",
                 pre_dest=stale_dest),
        Scenario("recv:update-W", build_recvtree, ["-a", "-W"], ["src/"], "dest/",
                 pre_dest=stale_dest),
        Scenario("recv:update-delete", build_recvtree, ["-a", "--delete"],
                 ["src/"], "dest/", pre_dest=stale_dest),
        Scenario("recv:delete-during", build_recvtree, ["-a", "--delete-during"],
                 ["src/"], "dest/", pre_dest=stale_dest),
        Scenario("recv:delete-after", build_recvtree, ["-a", "--delete-after"],
                 ["src/"], "dest/", pre_dest=stale_dest),
        Scenario("recv:delete-delay", build_recvtree, ["-a", "--delete-delay"],
                 ["src/"], "dest/", pre_dest=stale_dest),
        Scenario("recv:backup", build_recvtree, ["-a", "-b"], ["src/"], "dest/",
                 pre_dest=stale_dest),
        Scenario("recv:backup-dir", build_recvtree, ["-a", "-b", "--backup-dir=bak"],
                 ["src/"], "dest/", pre_dest=stale_dest),
        Scenario("recv:backup-suffix", build_recvtree, ["-a", "-b", "--suffix=.old"],
                 ["src/"], "dest/", pre_dest=stale_dest),
        Scenario("recv:inplace", build_recvtree, ["-a", "--inplace", "--no-whole-file"],
                 ["src/"], "dest/", pre_dest=stale_dest),
        Scenario("recv:delay-updates", build_recvtree, ["-a", "--delay-updates"],
                 ["src/"], "dest/", pre_dest=stale_dest),
        Scenario("recv:temp-dir", build_recvtree, ["-a", "--temp-dir=tmpd"],
                 ["src/"], "dest/", pre_dest=lambda d: (stale_dest(d), (d / "tmpd").mkdir(exist_ok=True))),
        Scenario("recv:partial-dir", build_recvtree, ["-a", "--partial-dir=.part"],
                 ["src/"], "dest/", pre_dest=stale_dest),
        Scenario("recv:fuzzy", build_recvtree, ["-a", "--fuzzy"], ["src/"], "dest/",
                 pre_dest=stale_dest),
        # dest-variant basis lookups (basis = identical sibling dir)
        Scenario("recv:link-dest-rel", setup_with_basis, ["-a", "--link-dest=../basis"],
                 ["src/"], "dest/"),
        Scenario("recv:link-dest-abs", setup_with_basis, ["-a"],
                 lambda wd: ["--link-dest=" + str(Path(wd) / "basis"), "src/"], "dest/"),
        Scenario("recv:compare-dest-rel", setup_with_basis,
                 ["-a", "--compare-dest=../basis"], ["src/"], "dest/"),
        Scenario("recv:copy-dest-rel", setup_with_basis,
                 ["-a", "--copy-dest=../basis"], ["src/"], "dest/"),
    ]
    return lift_transports(s)


def destshape_sweep():
    """Destination path shapes (symlinked dest dir, --mkpath) that stress the
    receiver's destination-path handling."""
    def symlinked_dest(dest: Path):
        real = Path(str(dest) + "_real")
        real.mkdir(parents=True, exist_ok=True)
        if not dest.is_symlink():
            os.symlink(real.name, dest)          # dest -> dest_X_real (in-tree)

    def real_of(dest: Path):
        return Path(str(dest) + "_real")

    def via_symlink_parent(dest: Path):
        # dest = .../dest_X ; make its PARENT route through an in-tree symlink:
        # realbase/, link->realbase, and rsync writes to link/<dest_X name>
        real = Path(str(dest) + "_rb")
        real.mkdir(parents=True, exist_ok=True)
        link = Path(str(dest) + "_lnk")
        if not link.is_symlink():
            os.symlink(real.name, link)
        return link

    s = [
        Scenario("dest:symlinked-dir", build_recvtree, ["-a"], ["src/"], "dest/",
                 dest_prep=symlinked_dest, snap_dest=real_of),
        Scenario("dest:mkpath", build_recvtree, ["-a", "--mkpath"], ["src/"],
                 "dest/new/deep/", snap_dest=lambda d: d),
    ]
    return s


def name_sweep():
    """Unusual but benign filenames (arg-handling / secluded-args)."""
    names = ["a space", "two  spaces", "café_ünïcode", "semi;colon",
             "dollar$sign", "paren(s)", "quote'name", "amp&and", "back\\slash",
             "newline\nname", "tab\tname", "trailing ", "leaddash"]

    def setup(src: Path):
        for i, n in enumerate(names):
            _write(src / n, f"content {i}\n".encode())
        # a leading-dash file (separate so it can't be mistaken for an option)
        _write(src / "-leadingdash.txt", b"dash\n")
        os.symlink("a space", src / "link to spaced")

    return [Scenario("name:weird", setup, ["-a"], ["src/"], "dest/")]


def filesfrom_sweep():
    """--files-from with relative & absolute name lists, and --from0.

    The list files live in the workdir, which is also rsync's cwd, so the
    --files-from arg is a BARE filename (cwd-relative) -- referencing it via
    str(wd) double-resolves and silently fails when --workdir is relative, which
    makes the whole scenario a vacuous pass. The absolute list uses src.resolve()
    so the "/" transfer-root case works regardless of workdir."""
    def setup(src: Path):
        build_recvtree(src)
        wd = src.parent
        asrc = src.resolve()
        (wd / "list_rel.txt").write_text("file1.txt\ndir/file2.txt\nslink\n")
        (wd / "list_abs.txt").write_text(
            f"{asrc}/file1.txt\n{asrc}/dir/file2.txt\n")
        (wd / "list0.txt").write_bytes(b"file1.txt\0dir/file2.txt\0")

    s = [
        Scenario("ff:rel", setup, ["-a"],
                 ["--files-from=list_rel.txt", "src/"], "dest/"),
        Scenario("ff:rel-R", setup, ["-aR"],
                 ["--files-from=list_rel.txt", "src/"], "dest/"),
        Scenario("ff:abs", setup, ["-a"],
                 ["--files-from=list_abs.txt", "/"], "dest/"),
        Scenario("ff:from0", setup, ["-a", "--from0"],
                 ["--files-from=list0.txt", "src/"], "dest/"),
    ]
    return lift_transports(s)


def build_privtree(src: Path):
    """Root-only fixture: owned files, special perms, FIFO, devices. Falls back
    gracefully to what the euid can create."""
    build_recvtree(src)
    os.chmod(src / "file1.txt", 0o4755)   # setuid
    os.chmod(src / "dir", 0o2755)         # setgid
    try:
        os.mkfifo(src / "fifo")
    except OSError:
        pass
    if os.geteuid() == 0:
        try:
            os.mknod(src / "chardev", stat.S_IFCHR | 0o644, os.makedev(1, 3))
            os.mknod(src / "blockdev", stat.S_IFBLK | 0o644, os.makedev(7, 0))
        except OSError:
            pass
        # chown a file to a different uid/gid if any exists
        try:
            os.chown(src / "dir/file2.txt", 1, 1)
        except OSError:
            pass


def priv_sweep():
    """Run as root (sudo): owner/group, devices/specials, fake-super."""
    return [
        Scenario("priv:archive", build_privtree, ["-a"], ["src/"], "dest/"),
        Scenario("priv:devices", build_privtree, ["-aD"], ["src/"], "dest/"),
        Scenario("priv:HD", build_privtree, ["-aHD"], ["src/"], "dest/"),
        Scenario("priv:numeric-ids", build_privtree, ["-a", "--numeric-ids"],
                 ["src/"], "dest/"),
        Scenario("priv:fake-super", build_privtree, ["-a", "--fake-super"],
                 ["src/"], "dest/"),
        Scenario("priv:specials", build_privtree, ["-a", "--specials"],
                 ["src/"], "dest/"),
        Scenario("priv:acls-xattrs", build_privtree, ["-aAX"], ["src/"], "dest/"),
    ]


def intree_sweep():
    """Traverse an IN-TREE dir-symlink as a path component (source via a
    dir-symlink, dest through a dir-symlink, keep-dirlinks, alt-basis via a
    dir-symlink) -- a path-handling shape that varies across builds/platforms."""
    def src_dirlink(src: Path):
        _write(src / "real/f1", b"in real\n")
        _write(src / "real/sub/f2", b"deep\n")
        os.symlink("real", src / "link")

    def dirtree(src: Path):
        _write(src / "dir/f1", b"one\n")
        _write(src / "dir/f2", b"two\n")
        _write(src / "top.txt", b"top\n")

    def dest_dirlink_prep(dest: Path):
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "real").mkdir(exist_ok=True)
        if not (dest / "link").is_symlink():
            os.symlink("real", dest / "link")

    def keepdir_prep(dest: Path):
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "realdir").mkdir(exist_ok=True)
        if not (dest / "dir").is_symlink():
            os.symlink("realdir", dest / "dir")

    def basis_dirlink(src: Path):
        build_recvtree(src)
        build_recvtree(src.parent / "realbasis")
        os.symlink("realbasis", src.parent / "basislink")

    return [
        Scenario("intree:src-via-dirlink", src_dirlink, ["-a"], ["src/link/"], "dest/"),
        Scenario("intree:src-dirlink-noslash", src_dirlink, ["-a"], ["src/link"], "dest/"),
        Scenario("intree:dest-via-dirlink", build_recvtree, ["-a"], ["src/"], "dest/",
                 dest_prep=dest_dirlink_prep,
                 dest_arg=lambda d: str(d / "link") + "/",
                 snap_dest=lambda d: d / "real"),
        Scenario("intree:keep-dirlinks", dirtree, ["-aK"], ["src/"], "dest/",
                 dest_prep=keepdir_prep, snap_dest=lambda d: d / "realdir"),
        Scenario("intree:link-dest-dirlink", basis_dirlink, ["-a"],
                 lambda wd: ["--link-dest=" + str(Path(wd) / "basislink"), "src/"],
                 "dest/"),
        Scenario("intree:compare-dest-dirlink", basis_dirlink, ["-a"],
                 lambda wd: ["--compare-dest=" + str(Path(wd) / "basislink"), "src/"],
                 "dest/"),
    ]


def intree2_sweep():
    """More in-tree dir-symlink traversal: -k/copy-dirlinks on the source,
    source files under a symlinked PARENT, and -K update/delete through a
    symlinked dest dir."""
    def src_with_dirlink(src: Path):
        _write(src / "realdir/a", b"aa\n")
        _write(src / "realdir/b", b"bb\n")
        _write(src / "top.txt", b"top\n")
        os.symlink("realdir", src / "dl")

    def src_symlink_parent(src: Path):
        _write(src / "real/sub/file", b"under symlinked parent\n")
        os.symlink("real", src / "link")

    def kupd_prep(dest: Path):
        # symlinked dest dir whose real target holds an OLDER file + an extra
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "realdir").mkdir(exist_ok=True)
        _write(dest / "realdir/f1", b"OLD\n")
        _ut(dest / "realdir/f1", T_OLD)
        _write(dest / "realdir/extra", b"extra\n")
        _ut(dest / "realdir/extra", T_OLD)
        if not (dest / "dir").is_symlink():
            os.symlink("realdir", dest / "dir")

    def dirtree2(src: Path):
        _write(src / "dir/f1", b"new1\n")
        _write(src / "dir/f2", b"new2\n")
        _ut(src / "dir/f1"); _ut(src / "dir/f2"); _ut(src / "dir")

    return [
        Scenario("intree2:copy-dirlinks-k", src_with_dirlink, ["-a", "-k"],
                 ["src/"], "dest/"),
        Scenario("intree2:copy-links-L", src_with_dirlink, ["-a", "-L"],
                 ["src/"], "dest/"),
        Scenario("intree2:src-symlink-parent", src_symlink_parent, ["-a"],
                 ["src/link/sub/file"], "dest/"),
        Scenario("intree2:src-symlink-parent-R", src_symlink_parent, ["-aR"],
                 ["src/link/sub/file"], "dest/"),
        Scenario("intree2:src-symlink-parent-dir", src_symlink_parent, ["-a"],
                 ["src/link/sub/"], "dest/"),
        Scenario("intree2:K-update", dirtree2, ["-aK"], ["src/"], "dest/",
                 dest_prep=kupd_prep, snap_dest=lambda d: d / "realdir"),
        Scenario("intree2:K-delete", dirtree2, ["-aK", "--delete"], ["src/"],
                 "dest/", dest_prep=kupd_prep, snap_dest=lambda d: d / "realdir"),
    ]


def proto_sweep():
    """Older protocol versions (negotiation / wire-format regressions)."""
    s = []
    for p in (29, 30, 31):
        for opt in (["-a"], ["-aH"], ["-aHS"], ["-az"],
                    ["-a", "--no-inc-recursive"]):
            tag = "+".join(o.lstrip("-") for o in opt if o != "-a") or "a"
            s.append(Scenario(f"proto{p}:{tag}", build_kitchen,
                     opt + [f"--protocol={p}"], ["src/"], "dest/"))
    return s


def combo_sweep():
    """Pairs of options over a stale dest (so update/backup/inplace actually
    fire) -- non-symlink option-interaction regressions."""
    import itertools
    flags = ["-H", "-S", "--inplace", "-z", "-c", "-b", "-O", "-J",
             "--numeric-ids", "-A", "-X", "-E", "--no-whole-file", "-I",
             "--size-only", "-u"]
    s = []
    for x, y in itertools.combinations(flags, 2):
        s.append(Scenario(f"combo:{x},{y}", build_recvtree, ["-a", x, y],
                          ["src/"], "dest/", pre_dest=stale_dest))
    return s


def scale_sweep():
    """Content scale: many small files, deep nesting, a large file."""
    def many(src: Path):
        for i in range(500):
            _write(src / f"d{i % 12}" / f"f{i:04d}", f"file {i}\n".encode())

    def deep(src: Path):
        p = src
        for i in range(40):
            p = p / f"d{i}"
        _write(p / "leaf", b"deep\n")
        _write(src / "shallow", b"s\n")

    def big(src: Path):
        _write(src / "big.bin", bytes((i * 7) & 0xFF for i in range(3_000_000)))
        _write(src / "small", b"x\n")

    return [
        Scenario("scale:many", many, ["-a"], ["src/"], "dest/"),
        Scenario("scale:many-H", many, ["-aH"], ["src/"], "dest/"),
        Scenario("scale:deep", deep, ["-a"], ["src/"], "dest/"),
        Scenario("scale:big", big, ["-a"], ["src/"], "dest/"),
        Scenario("scale:big-inplace", big, ["-a", "--inplace", "--no-whole-file"],
                 ["src/"], "dest/"),
        Scenario("scale:big-z", big, ["-az"], ["src/"], "dest/"),
    ]


def _daemon_scns(chroot):
    """PUSH scenarios to a [m] daemon module (the daemon receiver path)."""
    D = {"chroot": chroot}
    pfx = "daemonchroot" if chroot == "yes" else "daemon"

    def kt(src: Path):
        _write(src / "dir/f1", b"n1\n")
        _write(src / "dir/f2", b"n2\n")
        _write(src / "top", b"t\n")

    def kprep(dest: Path):
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "realdir").mkdir(exist_ok=True)
        if not (dest / "dir").is_symlink():
            os.symlink("realdir", dest / "dir")

    return [
        Scenario(f"{pfx}:push", build_recvtree, ["-a"], ["src/"], daemon=D),
        Scenario(f"{pfx}:push-H", build_recvtree, ["-aH"], ["src/"], daemon=D),
        Scenario(f"{pfx}:push-X", build_recvtree, ["-aX"], ["src/"], daemon=D),
        Scenario(f"{pfx}:push-update", build_recvtree, ["-a"], ["src/"],
                 pre_dest=stale_dest, daemon=D),
        Scenario(f"{pfx}:push-delete", build_recvtree, ["-a", "--delete"],
                 ["src/"], pre_dest=stale_dest, daemon=D),
        Scenario(f"{pfx}:push-K-symlinkdir", kt, ["-aK"], ["src/"],
                 dest_prep=kprep, snap_dest=lambda d: d / "realdir", daemon=D),
    ]


def daemon_sweep():
    return _daemon_scns("no")


def daemonchroot_sweep():
    """use chroot = yes (root-only): the daemon chroots into the module path."""
    return _daemon_scns("yes")


def gaps_sweep():
    """Scenarios observable mainly via the stdout/stderr/itemize signals rather
    than the dest tree: --list-only listings, --dry-run plans (incl.
    --mkpath+--dry-run file-to-file), and type-change updates."""
    def t_tree(src):
        _mk_reg(src / "f", 100)
        _mk_reg(src / "dir/g", 50)
        os.symlink("f", src / "l")
        _ut(src / "dir")

    def t_one(src):
        _mk_reg(src / "file", 50)

    def t_typesrc(src):
        _mk_reg(src / "x", 30)        # src: x is a FILE
        _mk_reg(src / "keep", 10)

    def pre_typedir(dest):            # dest: x is a DIR (different type)
        os.makedirs(dest / "x")
        _mk_reg(dest / "x" / "inner", 5)
        _ut(dest / "x")

    return [
        Scenario("gap:list-only", t_tree, ["--list-only", "-a"], ["src/"], "dest/"),
        Scenario("gap:dry-run", t_tree, ["-ai", "--dry-run"], ["src/"], "dest/"),
        Scenario("gap:mkpath-dryrun-f2f", t_one, ["-ai", "--mkpath", "--dry-run"],
                 ["src/file"], "dest/", dest_arg=lambda d: str(d) + "/newdir/file"),
        Scenario("gap:dry-run-delete", build_recvtree, ["-ai", "--dry-run", "--delete"],
                 ["src/"], "dest/", pre_dest=stale_dest),
        Scenario("gap:typechange-force", t_typesrc, ["-a", "--force"], ["src/"], "dest/",
                 pre_dest=pre_typedir),
        Scenario("gap:typechange-delete", t_typesrc, ["-a", "--delete"], ["src/"], "dest/",
                 pre_dest=pre_typedir),
    ]


def misc_sweep():
    """Genuinely-untested subsystems: filter/include-exclude rules, per-dir merge,
    CVS-exclude, fuzzy, write-batch, xattr, -R dot-anchoring -- looking for NEW
    root-cause families beyond the daemon-symlink cluster."""
    def t_filter(src):
        _mk_reg(src / "keep.txt", 10)
        _mk_reg(src / "skip.log", 10)
        _mk_reg(src / "sub/keep2.txt", 10)
        _mk_reg(src / "sub/skip2.log", 10)
        _ut(src / "sub")

    def t_dirmerge(src):
        _mk_reg(src / "a.txt", 10)
        _mk_reg(src / "b.log", 10)
        _write(src / ".rsync-filter", b"- *.log\n")
        _ut(src / ".rsync-filter")

    def t_cvs(src):
        _mk_reg(src / "keep", 10)
        _mk_reg(src / "core", 10)
        _mk_reg(src / "obj.o", 10)

    def t_fuzzy(src):
        _mk_reg(src / "file.txt", 5000, t=T_NEW)

    def pre_fuzzy(dest):
        _mk_reg(dest / "file.txt.bak", 5000, t=T_OLD, fill=7)

    def t_xattr(src):
        _mk_reg(src / "f", 10)
        try:
            os.setxattr(src / "f", "user.test", b"val")
        except OSError:
            pass

    def t_reldot(src):
        _mk_reg(src / "sub/deep/f", 10)
        _ut(src / "sub/deep")
        _ut(src / "sub")

    return [
        Scenario("misc:exclude", t_filter, ["-a", "--exclude=*.log"], ["src/"], "dest/"),
        Scenario("misc:filter-rule", t_filter, ["-a", "-f", "- *.log"], ["src/"], "dest/"),
        Scenario("misc:filter-incl", t_filter, ["-a", "-f", "+ */", "-f", "+ *.txt", "-f", "- *"], ["src/"], "dest/"),
        Scenario("misc:dirmerge-F", t_dirmerge, ["-a", "-F"], ["src/"], "dest/"),
        Scenario("misc:cvs-C", t_cvs, ["-a", "-C"], ["src/"], "dest/"),
        Scenario("misc:fuzzy", t_fuzzy, ["-a", "--fuzzy"], ["src/"], "dest/", pre_dest=pre_fuzzy),
        Scenario("misc:write-batch", build_recvtree,
                 (lambda wd, dest: ["-a", f"--write-batch={wd}/batch"]), ["src/"], "dest/"),
        Scenario("misc:xattr", t_xattr, ["-aX"], ["src/"], "dest/"),
        Scenario("misc:relative-dot", t_reldot, ["-aR"], ["src/./sub/deep/f"], "dest/"),
    ]


def daemon_sym_sweep():
    """PUSH symlink/dirlink/keep-dirlinks scenarios to a daemon module -- the
    daemon receiver/keep-dirlinks path. Enumerates the family: -K through a
    symlinked dest dir (plain/nested/update/delete), and -L/-k/-l/--safe-links/
    --munge-links/--copy-unsafe-links of in-tree and out-of-tree symlinks pushed
    into a module."""
    D = {"chroot": "no"}

    def kt(src):
        _mk_reg(src / "dir/f1", 10)
        _mk_reg(src / "dir/f2", 10)
        _mk_reg(src / "top", 5)

    def kt_nested(src):
        _mk_reg(src / "dir/sub/f", 10)
        _mk_reg(src / "top", 5)

    def kprep(dest):
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "realdir").mkdir(exist_ok=True)
        if not (dest / "dir").is_symlink():
            os.symlink("realdir", dest / "dir")

    def kprep_nested(dest):
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "realdir" / "sub").mkdir(parents=True, exist_ok=True)
        if not (dest / "dir").is_symlink():
            os.symlink("realdir", dest / "dir")

    def kprep_update(dest):
        kprep(dest)
        _mk_reg(dest / "realdir" / "f1", 3, t=T_OLD)

    def kprep_delete(dest):
        kprep(dest)
        _mk_reg(dest / "realdir" / "extra", 3)

    def src_dirlink(src):
        _mk_reg(src / "rd/inner", 10)
        os.symlink("rd", src / "dl")
        _mk_reg(src / "anchor", 5)
        _ut(src / "rd")

    def src_abslink(src):
        os.symlink("/etc/hostname", src / "abs")
        _mk_reg(src / "anchor", 5)

    rd = lambda d: d / "realdir"
    return [
        Scenario("dsym:K-dirlink", kt, ["-aK"], ["src/"], "dest/", dest_prep=kprep, snap_dest=rd, daemon=D),
        Scenario("dsym:K-nested", kt_nested, ["-aK"], ["src/"], "dest/", dest_prep=kprep_nested, snap_dest=rd, daemon=D),
        Scenario("dsym:K-update", kt, ["-aK"], ["src/"], "dest/", dest_prep=kprep_update, snap_dest=rd, daemon=D),
        Scenario("dsym:K-delete", kt, ["-aK", "--delete"], ["src/"], "dest/", dest_prep=kprep_delete, snap_dest=rd, daemon=D),
        Scenario("dsym:L-src-dirlink", src_dirlink, ["-aL"], ["src/"], "dest/", daemon=D),
        Scenario("dsym:k-src-dirlink", src_dirlink, ["-ak"], ["src/"], "dest/", daemon=D),
        Scenario("dsym:l-src", src_dirlink, ["-al"], ["src/"], "dest/", daemon=D),
        Scenario("dsym:safe-links", src_abslink, ["-a", "--safe-links"], ["src/"], "dest/", daemon=D),
        Scenario("dsym:munge", src_abslink, ["-a", "--munge-links"], ["src/"], "dest/", daemon=D),
        Scenario("dsym:copy-unsafe", src_abslink, ["-a", "--copy-unsafe-links"], ["src/"], "dest/", daemon=D),
    ]


def daemon_escape_sweep():
    """Daemon following symlinks that point OUTSIDE the module (absolute, or ../
    escape), via -L / --copy-links / --copy-unsafe-links / --safe-links, on both
    the sender (pull) and receiver (push) side -- the daemon symlink-safety
    behaviour."""
    Dpull = {"chroot": "no", "pull": True}
    Dpush = {"chroot": "no"}

    def s_abs(src):
        os.symlink("/etc/hostname", src / "abslnk")
        _mk_reg(src / "anchor", 5)

    def s_escape(src):
        _mk_reg(src.parent / "secret", 7)          # outside the module (wd/secret)
        os.symlink("../secret", src / "esc")
        _mk_reg(src / "anchor", 5)

    def s_filelink(src):
        _mk_reg(src / "real", 10)
        os.symlink("real", src / "fl")             # in-tree symlink to a FILE
        _mk_reg(src / "anchor", 5)

    def s_absdir(src):
        out = src.parent / "outdir"                # small out-of-module dir (wd/outdir)
        _mk_reg(out / "x", 8)
        _mk_reg(out / "y", 8)
        os.symlink(str(out), src / "extdir")       # absolute symlink to out-of-module DIR
        _mk_reg(src / "anchor", 5)

    pull = [
        ("dpull:L-abs", s_abs, ["-aL"]),
        ("dpull:L-escape", s_escape, ["-aL"]),
        ("dpull:copyunsafe-escape", s_escape, ["-a", "--copy-unsafe-links"]),
        ("dpull:L-filelink", s_filelink, ["-aL"]),
        ("dpull:safe-escape", s_escape, ["-a", "--safe-links"]),
        ("dpull:copylinks-abs", s_abs, ["-a", "--copy-links"]),
        ("dpull:L-absdir", s_absdir, ["-aL"]),
        ("dpull:k-absdir", s_absdir, ["-ak"]),
        ("dpull:copydirlinks-absdir", s_absdir, ["-a", "--copy-dirlinks"]),
        ("dpull:copyunsafe-absdir", s_absdir, ["-a", "--copy-unsafe-links"]),
    ]
    push = [
        ("dpush:L-abs", s_abs, ["-aL"]),
        ("dpush:L-escape", s_escape, ["-aL"]),
        ("dpush:copyunsafe-abs", s_abs, ["-a", "--copy-unsafe-links"]),
        ("dpush:copyunsafe-escape", s_escape, ["-a", "--copy-unsafe-links"]),
        ("dpush:L-filelink", s_filelink, ["-aL"]),
    ]
    s = [Scenario(n, fn, o, ["src/"], "dest/", daemon=Dpull) for n, fn, o in pull]
    s += [Scenario(n, fn, o, ["src/"], "dest/", daemon=Dpush) for n, fn, o in push]
    return s


def daemon_pull_sym_sweep():
    """PULL symlink/dirlink scenarios FROM a daemon module (the daemon SENDER
    side -- untested until now). Served source contains the symlinks; the client
    pulls with -L/-k/-l/-K/--safe-links/--munge-links/--copy-unsafe-links."""
    D = {"chroot": "no", "pull": True}

    def served_dirlink(src):
        _mk_reg(src / "rd/inner", 10)
        os.symlink("rd", src / "dl")
        _mk_reg(src / "anchor", 5)
        _ut(src / "rd")

    def served_intree(src):
        _mk_reg(src / "real", 10)
        os.symlink("real", src / "lnk")
        _mk_reg(src / "anchor", 5)

    def served_abslink(src):
        os.symlink("/etc/hostname", src / "abs")
        _mk_reg(src / "anchor", 5)

    def served_dir(src):
        _mk_reg(src / "dir/f1", 10)
        _mk_reg(src / "dir/f2", 10)
        _mk_reg(src / "top", 5)

    def kprep(dest):
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "realdir").mkdir(exist_ok=True)
        if not (dest / "dir").is_symlink():
            os.symlink("realdir", dest / "dir")

    return [
        Scenario("dpull:plain", build_recvtree, ["-a"], ["src/"], "dest/", daemon=D),
        Scenario("dpull:L-dirlink", served_dirlink, ["-aL"], ["src/"], "dest/", daemon=D),
        Scenario("dpull:k-dirlink", served_dirlink, ["-ak"], ["src/"], "dest/", daemon=D),
        Scenario("dpull:l", served_dirlink, ["-al"], ["src/"], "dest/", daemon=D),
        Scenario("dpull:L-intree", served_intree, ["-aL"], ["src/"], "dest/", daemon=D),
        Scenario("dpull:safe-links", served_abslink, ["-a", "--safe-links"], ["src/"], "dest/", daemon=D),
        Scenario("dpull:munge", served_abslink, ["-a", "--munge-links"], ["src/"], "dest/", daemon=D),
        Scenario("dpull:copy-unsafe", served_abslink, ["-a", "--copy-unsafe-links"], ["src/"], "dest/", daemon=D),
        Scenario("dpull:K-dest-dirlink", served_dir, ["-aK"], ["src/"], "dest/", dest_prep=kprep, snap_dest=lambda d: d / "realdir", daemon=D),
        Scenario("dpull:hardlinks", build_recvtree, ["-aH"], ["src/"], "dest/", daemon=D),
    ]


_COMBO_FLAGS = ["-H", "-S", "--inplace", "-z", "-c", "-b", "-O", "-J",
                "--numeric-ids", "-A", "-X", "-E", "--no-whole-file", "-I",
                "--size-only", "-u"]


def combo3_sweep():
    """Option TRIPLES over a stale dest -- deeper interaction coverage."""
    import itertools
    return [Scenario(f"combo3:{x},{y},{z}", build_recvtree, ["-a", x, y, z],
                     ["src/"], "dest/", pre_dest=stale_dest)
            for x, y, z in itertools.combinations(_COMBO_FLAGS, 3)]


def combo4_sweep():
    """Option QUADRUPLES over a stale dest (C(16,4)=1820)."""
    import itertools
    return [Scenario(f"combo4:{w},{x},{y},{z}", build_recvtree,
                     ["-a", w, x, y, z], ["src/"], "dest/", pre_dest=stale_dest)
            for w, x, y, z in itertools.combinations(_COMBO_FLAGS, 4)]


def ssh_sweep():
    """PUSH over a remote-shell split (support/lsh.sh) -- exercises the real
    client+server processes / protocol / arg passing, and confirms whether the
    in-tree-symlink regressions also manifest over the wire."""
    def kt(src: Path):
        _write(src / "dir/f1", b"n1\n")
        _write(src / "dir/f2", b"n2\n")
        _write(src / "top", b"t\n")

    def kprep(dest: Path):
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "realdir").mkdir(exist_ok=True)
        if not (dest / "dir").is_symlink():
            os.symlink("realdir", dest / "dir")

    def sym_parent(src: Path):
        _write(src / "real/sub/file", b"under symlinked parent\n")
        os.symlink("real", src / "link")

    return [
        Scenario("ssh:push", build_recvtree, ["-a"], ["src/"], ssh=True),
        Scenario("ssh:push-H", build_recvtree, ["-aH"], ["src/"], ssh=True),
        Scenario("ssh:push-X", build_recvtree, ["-aX"], ["src/"], ssh=True),
        Scenario("ssh:push-z", build_recvtree, ["-az"], ["src/"], ssh=True),
        Scenario("ssh:push-update", build_recvtree, ["-a"], ["src/"],
                 pre_dest=stale_dest, ssh=True),
        Scenario("ssh:push-delete", build_recvtree, ["-a", "--delete"], ["src/"],
                 pre_dest=stale_dest, ssh=True),
        Scenario("ssh:push-protect-args", build_recvtree, ["-a", "-s"], ["src/"],
                 ssh=True),
        Scenario("ssh:K-symlinkdir", kt, ["-aK"], ["src/"], dest_prep=kprep,
                 snap_dest=lambda d: d / "realdir", ssh=True),
        Scenario("ssh:R-symlink-parent", sym_parent, ["-aR"],
                 ["src/link/sub/file"], ssh=True),
    ]


def redo_sweep():
    """Resume / redo state machine: a partial or corrupted prior dest forces the
    delta + verify + resume path (inplace / append-verify / partial-dir, the
    latter both relative AND absolute). Generalises the single-pass model -- the
    'failed verification, update discarded' loop and the discard-path NULL-deref
    both live here. Lifted across transports too."""
    def big(src: Path):
        _mk_reg(src / "f", 120000, t=T_NEW, fill=7)
        _mk_reg(src / "keep", 200, t=T_NEW, fill=3)

    def pre_truncated(dest: Path):        # a shorter prefix of f (older) -> extend
        _mk_reg(dest / "f", 40000, t=T_OLD, fill=7)

    def pre_corrupt(dest: Path):          # same size, WRONG content -> delta+verify
        _mk_reg(dest / "f", 120000, t=T_OLD, fill=200)

    def pre_abs_partial(dest: Path):      # corrupt dest + a stale leftover in an
        pre_corrupt(dest)                 # ABSOLUTE partial-dir (delta-resume shape)
        pdir = dest.parent / (dest.name + "_part")
        pdir.mkdir(parents=True, exist_ok=True)
        _mk_reg(pdir / "f", 60000, t=T_OLD, fill=7)

    abs_part = lambda wd, dest: ["-a", "--no-whole-file", "--partial",
                                 f"--partial-dir={Path(dest).parent}/{Path(dest).name}_part"]
    s = [
        Scenario("redo:inplace-corrupt", big, ["-a", "--inplace", "--no-whole-file"],
                 ["src/"], "dest/", pre_dest=pre_corrupt),
        Scenario("redo:append-verify", big, ["-a", "--append-verify"],
                 ["src/"], "dest/", pre_dest=pre_truncated),
        Scenario("redo:append", big, ["-a", "--append"],
                 ["src/"], "dest/", pre_dest=pre_truncated),
        Scenario("redo:partialdir-rel", big,
                 ["-a", "--no-whole-file", "--partial", "--partial-dir=.part"],
                 ["src/"], "dest/", pre_dest=pre_corrupt),
        Scenario("redo:checksum-corrupt", big, ["-a", "-c", "--no-whole-file"],
                 ["src/"], "dest/", pre_dest=pre_corrupt),
    ]
    s = lift_transports(s)                # resume path over the wire too
    # absolute partial-dir uses callable opts (not liftable) -> keep local
    s.append(Scenario("redo:partialdir-abs", big, abs_part, ["src/"], "dest/",
                      pre_dest=pre_abs_partial))
    return s


def typetrans_sweep():
    """Type-transition: the existing dest entry has a DIFFERENT type than the
    source (file/dir/symlink/fifo), crossed with the selection options whose job
    is to decide whether to replace it. Generalises 'update skips a file of a
    different type'. The stale-dest sweeps only ever vary content, never type."""
    def src_file(src: Path):
        _mk_reg(src / "x", 60, t=T_NEW)
        _mk_reg(src / "keep", 10, t=T_NEW)

    def src_dir(src: Path):
        _mk_reg(src / "x/inner", 30, t=T_NEW)
        _mk_reg(src / "keep", 10, t=T_NEW)
        _ut(src / "x")

    def src_link(src: Path):
        _mk_reg(src / "real", 30, t=T_NEW)
        os.symlink("real", src / "x")
        _mk_reg(src / "keep", 10, t=T_NEW)

    # dest entries are pinned NEWER than the source (T_NEWER) so the -u
    # update-skip path is actually exercised across the type change, and so the
    # special-file mtimes are deterministic (unpinned fifos/symlinks otherwise
    # carry wall-clock creation time -> spurious A/B mtime diffs).
    T_NEWER = T_NEW + 10**7

    def d_dir(dest: Path):
        os.makedirs(dest / "x")
        _mk_reg(dest / "x/old", 5, t=T_NEWER)
        _ut(dest / "x", T_NEWER)

    def d_file(dest: Path):
        _mk_reg(dest / "x", 99, t=T_NEWER, fill=200)

    def d_link(dest: Path):
        os.symlink("keep", dest / "x")
        os.utime(dest / "x", (T_NEWER, T_NEWER), follow_symlinks=False)

    def d_fifo(dest: Path):
        _mk_fifo(dest / "x")
        os.utime(dest / "x", (T_NEWER, T_NEWER))

    srcs = [("file", src_file), ("dir", src_dir), ("link", src_link)]
    dests = [("Ddir", d_dir), ("Dfile", d_file), ("Dlink", d_link), ("Dfifo", d_fifo)]
    opts = [("plain", ["-a"]), ("update", ["-a", "-u"]),
            ("existing", ["-a", "--existing"]),
            ("ignore-existing", ["-a", "--ignore-existing"]),
            ("force", ["-a", "--force"]), ("delete", ["-a", "--delete"])]
    s = []
    for sn, sf in srcs:
        for dn, df in dests:
            for on, ov in opts:
                # skip the same-type combo (file src vs file dest etc.)
                if (sn, dn) in (("file", "Dfile"), ("dir", "Ddir"), ("link", "Dlink")):
                    continue
                s.append(Scenario(f"type:{sn}-vs-{dn}-{on}", sf, ov, ["src/"],
                                  "dest/", pre_dest=df))
    return lift_transports(s)


def tsprec_sweep():
    """Timestamp precision: sub-second mtimes at nanosecond boundaries, crossed
    with the time-handling options. Exercises the nsec validate/convert path that
    integer-second fixtures never reach (caught via rc/error/content/itemize --
    snapshot records whole-second mtimes, so this is a code-path exerciser)."""
    NS = [0, 1, 4999, 500000000, 999999999, 999999001]

    def mk(src: Path, nsec):
        _mk_reg(src / "f", 100)
        os.utime(src / "f", ns=(T_NEW * 10**9 + nsec, T_NEW * 10**9 + nsec))
        os.makedirs(src / "d", exist_ok=True)
        _mk_reg(src / "d/g", 50)
        os.utime(src / "d/g", ns=(T_NEW * 10**9 + nsec, T_NEW * 10**9 + nsec))

    s = []
    for nsec in NS:
        s.append(Scenario(f"ts:nsec{nsec}", lambda src, n=nsec: mk(src, n),
                          ["-a"], ["src/"], "dest/"))
    s += [
        Scenario("ts:modwindow", lambda src: mk(src, 999999999),
                 ["-a", "--modify-window=1"], ["src/"], "dest/"),
        Scenario("ts:atimes", lambda src: mk(src, 123456789),
                 ["-a", "--atimes"], ["src/"], "dest/"),
        Scenario("ts:crtimes", lambda src: mk(src, 123456789),
                 ["-a", "--crtimes"], ["src/"], "dest/"),
    ]
    return s


def bigscale_sweep():
    """Scale escalation: many empty dirs / many files at SCALE_N (--scale). Pairs
    with the --cost peak-RSS oracle -- a per-entry allocation/footprint regression
    only shows up at scale and is invisible to functional outcome alone."""
    n = max(1, SCALE_N)

    def emptydirs(src: Path):
        for i in range(n):
            (src / f"d{i:07d}").mkdir()

    def manyfiles(src: Path):
        for i in range(n):
            _write(src / f"b{i % 64:02d}" / f"f{i:07d}", b"x\n")

    def deepdirs(src: Path):
        for i in range(n):
            (src / f"a{i % 50:02d}" / f"b{(i // 50) % 50:02d}" / f"c{i:07d}").mkdir(parents=True)

    return [
        Scenario("scale:emptydirs", emptydirs, ["-a"], ["src/"], "dest/"),
        Scenario("scale:emptydirs-nir", emptydirs, ["-a", "--no-inc-recursive"],
                 ["src/"], "dest/"),
        Scenario("scale:manyfiles", manyfiles, ["-a"], ["src/"], "dest/"),
        Scenario("scale:deepdirs", deepdirs, ["-a"], ["src/"], "dest/"),
    ]


def rrsync_sweep():
    """rrsync lane: route the remote side through the restricted rrsync wrapper
    (subdir-restricted, so its option/path validation is exercised) -- a whole
    subsystem the other lanes never drive. Covers `-a` and an `-rlpt` control,
    push and pull. rrsync ships per version, so each build is paired with its own
    rrsync via --rrsync-a/--rrsync-b."""
    bt = build_recvtree

    def tree(src: Path):
        _mk_reg(src / "dir/f1", 50)
        _mk_reg(src / "dir/f2", 50)
        _mk_reg(src / "top", 20)
        os.symlink("top", src / "lnk")
        _ut(src / "dir")

    def abslink(src: Path):
        _mk_reg(src / "anchor", 10)
        os.symlink("/etc/hostname", src / "abs")

    def mk(name, setup, opts, pull=False, pre_dest=None):
        sc = Scenario(name, setup, opts, ["src/"], "dest/", pre_dest=pre_dest)
        sc.rrsync = {"pull": pull}
        return sc

    return [
        mk("rr:push-a", bt, ["-a"]),                       # D-bundled -> subdir deny decides
        mk("rr:push-rlpt", tree, ["-rlpt"]),               # no-D control: should transfer
        mk("rr:push-rlptD", tree, ["-rlptD"]),             # explicit D into restricted subdir
        mk("rr:push-update", bt, ["-a"], pre_dest=stale_dest),
        mk("rr:push-copy-unsafe", abslink, ["-rlpt", "--copy-unsafe-links"]),
        mk("rr:pull-a", bt, ["-a"], pull=True),
        mk("rr:pull-rlpt", tree, ["-rlpt"], pull=True),
        mk("rr:pull-symlink", tree, ["-rlpt"], pull=True),
    ]


def tcpdaemon_sweep():
    """Real-TCP-daemon lane: a genuine `rsync --daemon` on a bound port (greeting/
    handshake/socket path), and an auth variant (challenge-response) -- the daemon
    code the stdio-pipe lane bypasses. Push + pull, with/without auth."""
    bt = build_recvtree

    def mk(name, opts, pull=False, auth=False, pre_dest=None):
        sc = Scenario(name, bt, opts, ["src/"], "dest/", pre_dest=pre_dest)
        sc.daemon = {"tcp": True, "pull": pull, "auth": auth}
        return sc

    return [
        mk("tcp:push", ["-a"]),
        mk("tcp:push-H", ["-aH"]),
        mk("tcp:push-X", ["-aX"]),
        mk("tcp:push-update", ["-a"], pre_dest=stale_dest),
        mk("tcp:push-delete", ["-a", "--delete"], pre_dest=stale_dest),
        mk("tcp:pull", ["-a"], pull=True),
        mk("tcp:pull-H", ["-aH"], pull=True),
        mk("tcp:push-auth", ["-a"], auth=True),
        mk("tcp:pull-auth", ["-a"], pull=True, auth=True),
    ]


SWEEPS = {"options": options_sweep, "pathshape": pathshape_sweep,
          "recv": recv_sweep, "destshape": destshape_sweep,
          "name": name_sweep, "filesfrom": filesfrom_sweep,
          "intree": intree_sweep, "intree2": intree2_sweep,
          "proto": proto_sweep, "combo": combo_sweep, "combo3": combo3_sweep,
          "combo4": combo4_sweep, "scale": scale_sweep, "ssh": ssh_sweep,
          "daemon": daemon_sweep, "daemonchroot": daemonchroot_sweep,
          "mode": mode_sweep, "size": size_sweep, "filetype": filetype_sweep,
          "selection": selection_sweep, "behavior": behavior_sweep,
          "placement": placement_sweep, "wire": wire_sweep,
          "pairwise": pairwise_sweep, "daemonsym": daemon_sym_sweep, "daemonpull": daemon_pull_sym_sweep, "daemonesc": daemon_escape_sweep, "misc": misc_sweep, "gaps": gaps_sweep,
          "redo": redo_sweep, "typetrans": typetrans_sweep, "tsprec": tsprec_sweep,
          "bigscale": bigscale_sweep, "rrsync": rrsync_sweep,
          "tcpdaemon": tcpdaemon_sweep,
          "priv": priv_sweep}
# "all" excludes the root-only sweeps (priv, daemonchroot) and the very large
# combo4; run those explicitly. Parallelism (-j) makes the broad benign set
# (incl. the daemon symlink/escape + misc/gaps sweeps) affordable by default;
# combo4 stays out of a single pass (the --loop ladder reaches order 4 anyway).
# bigscale stays out of the default pass (heavy + meant for --cost runs); reach
# it via --sweep bigscale --cost --scale N.
ALL_SWEEPS = ["options", "pathshape", "recv", "destshape", "name", "filesfrom",
              "intree", "intree2", "proto", "combo", "combo3", "scale", "ssh",
              "daemon", "mode", "size", "filetype", "selection", "behavior",
              "placement", "wire", "pairwise", "daemonsym", "daemonpull",
              "daemonesc", "misc", "gaps", "redo", "typetrans", "tsprec",
              "rrsync", "tcpdaemon"]


def _compare(a, b, has_times=True, ign_types=(), incl_item=True, incl_lit=True,
             incl_out=False, incl_err=False):
    """Issue list for one result vs another (A-vs-B, and the per-binary stability
    check). incl_item/incl_lit/incl_out/incl_err drop the itemize / Literal-data /
    normalised-stdout / normalised-stderr-text signals (used when those are
    themselves nondeterministic, or for content-only checks). Empty list ==
    indistinguishable on the requested signals."""
    issues = []
    if a["rc"] != b["rc"]:
        issues.append(f"  exit: A={a['rc']} B={b['rc']}")
    a_err = any(m in a["err"] for m in ERR_MARKERS)
    b_err = any(m in b["err"] for m in ERR_MARKERS)
    if a_err != b_err:
        issues.append(f"  stderr-error: A={a_err!r} B={b_err!r}\n     A:{a['err'][:300]}")
    if incl_lit and a["lit"] != b["lit"]:
        issues.append(f"  Literal-data: A={a['lit']} B={b['lit']}")
    issues += diff_snapshots(a["snap"], b["snap"], ignore_mtime=not has_times,
                             ignore_mtime_types=ign_types)
    if incl_item and a["item"] != b["item"]:
        issues.append("  itemize differs:\n     A=" + repr(a["item"])
                      + "\n     B=" + repr(b["item"]))
    if incl_err and a.get("errn", "") != b.get("errn", ""):
        issues.append("  stderr-text differs:\n     A:" + a.get("errn", "")[:300]
                      + "\n     B:" + b.get("errn", "")[:300])
    if incl_out and a.get("out", "") != b.get("out", ""):
        issues.append("  stdout differs:\n     A:" + a.get("out", "")[:300]
                      + "\n     B:" + b.get("out", "")[:300])
    return issues


def run_scenario(scn: Scenario, workroot: Path):
    wd = workroot / scn.name.replace("/", "_").replace(":", "_").replace("+", "_")
    if wd.exists():
        shutil.rmtree(wd, ignore_errors=True)
    # opts may be a list, or a callable(wd, dest)->list for placement options
    # whose aux dir is workdir/dest-relative (dest differs per A/B tag).  Resolve
    # a representative copy (dest_A) for flag inspection; resolve per-tag below.
    def resolve_opts(dest):
        return scn.opts(str(wd), str(dest)) if callable(scn.opts) else scn.opts
    opts0 = resolve_opts(wd / "dest_A")
    # skip if either binary lacks an option (can't A/B compare)
    for o in opts0:
        flag = o.split("=", 1)[0]
        if flag.startswith("--"):
            if not (supports(RSYNC_A, flag) and supports(RSYNC_B, flag)):
                return ("SKIP", f"a binary lacks {flag}")
    # build the source ONCE so both binaries see identical input (incl. mtimes)
    src = wd / "src"
    src.mkdir(parents=True, exist_ok=True)
    scn.setup(src)
    src_args = scn.src_args(str(wd)) if callable(scn.src_args) else scn.src_args
    # ignore mtime only when neither -t nor -a is in effect; some dest times are
    # left unmanaged by rsync (-O dirs, -J links, -b backup) -> ignore those.
    has_times = any(o in ("-a", "-t", "-rlptD", "--times") or
                    (o.startswith("-") and not o.startswith("--") and "t" in o)
                    for o in opts0)
    ign_types = _ign(opts0)
    # a "/"-rooted or absolute source (e.g. --files-from with absolute names)
    # makes rsync create IMPLIED parent dirs it has no source time for -> their
    # mtime is wall-clock and differs between the A and B runs. Ignore dir mtimes
    # for those, like -O/backup dirs.
    if isinstance(src_args, list) and any(a == "/" or a.startswith("/")
                                          for a in src_args):
        ign_types = set(ign_types) | {"d"}

    def one_run(tag, binary, rep):
        dest = wd / f"dest_{tag}{rep}"
        optr = resolve_opts(dest)     # per-tag (aux dir is under this tag's dest)
        if scn.dest_prep:
            scn.dest_prep(dest)        # e.g. create dest as a symlink to a real dir
        elif scn.pre_dest:
            dest.mkdir(parents=True, exist_ok=True)
            scn.pre_dest(dest)
        _tls.measure = COST            # have sh() sample peak process-group RSS
        _tls.rss = None
        if scn.daemon is not None:
            port = (20000 + (abs(hash(scn.name)) % 2000) * 16
                    + (0 if tag == "A" else 8) + rep)
            D = scn.daemon
            if D.get("tcp"):           # real bound TCP port (not the stdio pipe)
                rc, err, lit, item, out = _tcp_daemon(
                    binary, str(wd), str(wd / "src") if D.get("pull") else str(dest),
                    optr, src_args, localdest=str(dest), pull=D.get("pull", False),
                    chroot=D.get("chroot", "no"), auth=D.get("auth", False))
            elif D.get("pull"):
                rc, err, lit, item, out = run_daemon_pull(
                    binary, str(wd), str(wd / "src"), optr, str(dest), port,
                    chroot=D.get("chroot", "no"))
            else:
                rc, err, lit, item, out = run_daemon_xfer(
                    binary, str(wd), str(dest), optr, src_args, port,
                    chroot=D.get("chroot", "no"))
        elif scn.rrsync is not None:
            rrs = (RRSYNC_A if tag == "A" else RRSYNC_B) or str(_RRSYNC_SRC)
            if scn.rrsync.get("pull"):
                rc, err, lit, item, out = run_rrsync_pull(
                    binary, rrs, str(wd), str(wd / "src"), optr, str(dest))
            else:
                rc, err, lit, item, out = run_rrsync_push(
                    binary, rrs, str(wd), optr, src_args, str(dest))
        elif scn.ssh:
            rc, err, lit, item, out = run_ssh_xfer(binary, str(wd), optr,
                                                   src_args, str(dest))
        else:
            dest_arg = scn.dest_arg(dest) if scn.dest_arg else str(dest) + "/"
            rc, err, lit, item, out = run_xfer(binary, str(wd), optr, src_args,
                                               dest_arg)
        _tls.measure = False
        snap_target = scn.snap_dest(dest) if scn.snap_dest else dest
        return dict(rc=rc, err=err, lit=lit, item=item, snap=snapshot(snap_target),
                    out=_norm_out(out, wd, dest), errn=_norm_err(err, wd, dest),
                    rss=getattr(_tls, "rss", None))

    # STABILITY GATE: run each binary REPEAT times (cheap), and if a candidate
    # A/B diff appears, ESCALATE to more samples and require the diff to be stable
    # across ALL of them. A binary whose own runs disagree (or a diff that doesn't
    # reproduce) is nondeterministic -> quarantine FLAKY, never a false regression.
    # (A ~50% flake fools 2 repeats too often; escalation makes false DIFFs rare.)
    # CONTENT-level instability within one binary's repeats = real nondeterminism
    # (rc / error / dest content+existence; mtime, itemize, Literal-data excluded
    # -- those carry incidental wall-clock/dir-time variance, not a correctness
    # flake).  This is what makes an A/B diff untrustworthy -> quarantine FLAKY.
    def content_unstable(rs):
        for other in rs[1:]:
            d = _compare(rs[0], other, has_times=False, ign_types=set(),
                         incl_item=False, incl_lit=False)
            if d:
                return d
        return None

    def attr_stable(rs, k):
        return all(rs[0][k] == r[k] for r in rs[1:])

    base = max(1, REPEAT)
    ra = [one_run("A", RSYNC_A, r) for r in range(base)]
    rb = [one_run("B", RSYNC_B, r) for r in range(base)]
    item_ok = lit_ok = out_ok = err_ok = True
    if base >= 2:
        # escalate sampling when a candidate A/B diff appears (on ANY signal),
        # to confirm stability before trusting it
        if _compare(ra[0], rb[0], has_times, ign_types, incl_out=True, incl_err=True):
            confirm = max(base, 5)
            ra += [one_run("A", RSYNC_A, r) for r in range(base, confirm)]
            rb += [one_run("B", RSYNC_B, r) for r in range(base, confirm)]
        cu = content_unstable(ra) or content_unstable(rb)
        if cu:
            if not KEEP:
                shutil.rmtree(wd, ignore_errors=True)
            return ("FLAKY", ["  scenario content is nondeterministic across "
                              "repeats (quarantined, not a regression):"] + cu[:6])
        # itemize / Literal-data / stdout / stderr-text are kept as A/B signals
        # only if each is stable per binary (else incidental run-variance noise)
        item_ok = attr_stable(ra, "item") and attr_stable(rb, "item")
        lit_ok = attr_stable(ra, "lit") and attr_stable(rb, "lit")
        out_ok = attr_stable(ra, "out") and attr_stable(rb, "out")
        err_ok = attr_stable(ra, "errn") and attr_stable(rb, "errn")
    a, b = ra[0], rb[0]
    # When BOTH builds error on this (often edge) input, neither produced a clean
    # benign transfer; the exact stderr/stdout/itemize/literal wording is low
    # signal (different errno/message for the same failure). A real regression --
    # A worse than B -- still shows in the exit code and the dest tree, which stay
    # compared. Drop the free-text/itemize signals in that case.
    both_failed = (any(m in a["err"] for m in ERR_MARKERS)
                   and any(m in b["err"] for m in ERR_MARKERS))
    issues = _compare(a, b, has_times, ign_types,
                      incl_item=item_ok and not both_failed,
                      incl_lit=lit_ok and not both_failed,
                      incl_out=out_ok and not both_failed,
                      incl_err=err_ok and not both_failed)
    # COST oracle: directional peak-RSS blow-up. Require the gap to hold across
    # ALL samples (min A vs max B) so run-to-run RSS noise can't trip it; only a
    # gross ratio + absolute floor counts -- a real resource regression, not jitter.
    if COST:
        arss = [r["rss"] for r in ra if r.get("rss")]
        brss = [r["rss"] for r in rb if r.get("rss")]
        if arss and brss:
            amin, bmax = min(arss), max(brss)
            if amin > 3 * bmax and (amin - bmax) > 64 * 1024 * 1024:
                issues.append(f"  peak-RSS blow-up: A>={amin // (1<<20)}MB "
                              f"B<={bmax // (1<<20)}MB ({amin / bmax:.1f}x)")
    if not (KEEP or issues):
        shutil.rmtree(wd, ignore_errors=True)
    if not issues:
        return ("OK", issues)
    # Classify: if A's only divergence is an INTENTIONAL refusal that B did not
    # do (an entry in ALLOWLIST), mark ALLOW -- a documented behaviour change,
    # recorded separately, not a silent regression.
    for sub, note in ALLOWLIST:
        if sub in a["err"] and sub not in b["err"]:
            return ("ALLOW", [f"  intentional behaviour change: {note}",
                              f"     A:{a['err'][:200]}"] )
    # Direction matters for "regression": only A being WORSE than B counts.
    a_ok = a["rc"] == 0 and not any(m in a["err"] for m in ERR_MARKERS)
    b_ok = b["rc"] == 0 and not any(m in b["err"] for m in ERR_MARKERS)
    if a_ok and not b_ok:
        return ("ABETTER", ["  A succeeds where B FAILS -- an "
                            "improvement/behaviour change, not a regression:",
                            f"     B:{b['err'][:200]}"])
    return ("DIFF", issues)


_mport = [25500]


def _ssh1(cbin, sbin, wd, opts, src, dest, pull):
    e = ["-e", f"sh {_LSH}", f"--rsync-path={sbin}"]
    if pull:   # remote side (sbin) is the SENDER
        argv = [cbin, "--stats", "-i", *e, *opts, f"lh:{src}/", f"{dest}/"]
    else:      # client (cbin) is the SENDER
        argv = [cbin, "--stats", "-i", *e, *opts, f"{src}/", f"lh:{dest}/"]
    return _parse_out(sh(argv, cwd=str(wd)))


def _daemon1(cbin, sbin, wd, module, opts, localdir, pull):
    _mport[0] += 1
    port = _mport[0]
    conf = Path(wd) / f"d{port}.conf"
    Path(module).mkdir(parents=True, exist_ok=True)
    conf.write_text(f"use chroot = no\nport = {port}\nlog file = {wd}/d{port}.log\n"
                    f"pid file = {wd}/d{port}.pid\n[m]\n  path = {module}\n"
                    f"  read only = no\n  hosts allow = 127.0.0.1\n")
    proc = subprocess.Popen([sbin, "--daemon", "--no-detach", f"--config={conf}",
                             f"--port={port}", "--address=127.0.0.1"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        if not _wait_port(port):
            return (99, "daemon failed to start", None, "")
        url = f"rsync://127.0.0.1:{port}/m/"
        if pull:   # daemon (sbin) is the SENDER
            argv = [cbin, "--stats", "-i", *opts, url, f"{localdir}/"]
        else:      # client (cbin) is the SENDER
            argv = [cbin, "--stats", "-i", *opts, f"{localdir}/", url]
        return _parse_out(sh(argv, cwd=str(wd)))
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def run_matrix(workroot, logf):
    """Cross-build role matrix: mixed A<->B over the wire, both directions
    (push/pull => which build is sender vs receiver), both transports
    (ssh/daemon).  Each config's dest is compared to the PURE-B baseline; an
    A-involved config that is WORSE than pure-B is a regression (protocol/interop
    or role-specific)."""
    A, B = RSYNC_A, RSYNC_B
    scns = [("basic", build_recvtree, ["-a"]),
            ("H", build_recvtree, ["-aH"]),
            ("X", build_recvtree, ["-aX"]),
            ("z", build_recvtree, ["-az"]),
            ("c", build_recvtree, ["-ac"])]
    combos = [("Ac_As", A, A), ("Ac_Bs", A, B), ("Bc_As", B, A)]
    nreg = 0

    def one(cbin, sbin, wd, tag, transport, direction, opts):
        dest = Path(wd) / f"d_{tag}"
        if transport == "ssh":
            rc, err, lit, item, _out = _ssh1(cbin, sbin, wd, opts, Path(wd) / "src",
                                             dest, pull=(direction == "pull"))
        else:
            if direction == "pull":
                rc, err, lit, item, _out = _daemon1(cbin, sbin, wd, Path(wd) / "src",
                                                    opts, dest, pull=True)
            else:
                rc, err, lit, item, _out = _daemon1(cbin, sbin, wd, dest, opts,
                                                    Path(wd) / "src", pull=False)
        return rc, err, snapshot(dest)

    for name, setup, opts in scns:
        for transport in ("ssh", "daemon"):
            for direction in ("push", "pull"):
                wd = workroot / f"mx_{name}_{transport}_{direction}"
                shutil.rmtree(wd, ignore_errors=True)
                (wd / "src").mkdir(parents=True)
                setup(wd / "src")
                brc, berr, bsnap = one(B, B, wd, "base", transport, direction, opts)
                bok = brc == 0 and not any(m in berr for m in ERR_MARKERS)
                for tag, cv, sv in combos:
                    rc, err, snap = one(cv, sv, wd, tag, transport, direction, opts)
                    ok = rc == 0 and not any(m in err for m in ERR_MARKERS)
                    diffs = diff_snapshots(snap, bsnap)
                    label = f"matrix:{name}/{transport}/{direction}/{tag}"
                    if not diffs and ok == bok:
                        print(f"OK    {label}")
                        continue
                    if ok and not bok:
                        print(f"BETTER {label} (A cfg ok; pure-B failed)")
                        continue
                    nreg += 1
                    kind = "exit/err" if ok != bok else "tree"
                    print(f"DIFF  {label}  [{kind}; client={cv.split('/')[-1]} "
                          f"server={sv.split('/')[-1]}]")
                    detail = ([f"  exit/err: cfg ok={ok} (rc={rc}) base ok={bok}",
                               f"     err:{err[:200]}"] if ok != bok else []) + diffs[:8]
                    for ln in detail:
                        print(ln)
                    logf.write(f"\nMATRIX-DIFF {label}  opts:{' '.join(opts)} "
                               f"vs pure-B  *** REGRESSION CANDIDATE ***\n")
                    for ln in detail:
                        logf.write(ln + "\n")
                    logf.flush()
    print(f"\n=== matrix: {nreg} cross-version regression candidates ===")
    return nreg


def _fx_hardlinks(src):
    for i in range(6):
        _write(src / f"f{i}", f"data{i % 3}\n".encode())
    for i in range(6):
        try:
            os.link(src / f"f{i % 3}", src / f"hl{i}")
        except OSError:
            pass
    os.symlink("f0", src / "sl")


def _fx_weird(src):
    for n in ["a space", "café", "semi;colon", "dollar$x", "paren(s)", "-dash",
              "tab\tt"]:
        _write(src / n, b"x\n")


def _fx_deep(src):
    p = src
    for i in range(25):
        p = p / f"d{i}"
    _write(p / "leaf", b"deep\n")
    _write(src / "top", b"t\n")


def _fx_sparse(src):
    with open(src / "sp.bin", "wb") as f:
        f.seek(2 << 20)
        f.write(b"end")
    _write(src / "reg", b"r\n")


def _fx_many(src):
    for i in range(200):
        _write(src / f"d{i % 8}" / f"f{i:03d}", f"{i}\n".encode())


FUZZ_FIXTURES = [("recvtree", build_recvtree), ("kitchen", build_kitchen),
                 ("hardlinks", _fx_hardlinks), ("weird", _fx_weird),
                 ("deep", _fx_deep), ("sparse", _fx_sparse), ("many", _fx_many)]
FUZZ_FLAGS = ["-H", "-S", "--inplace", "-z", "-c", "-b", "-O", "-J",
              "--numeric-ids", "-A", "-X", "-E", "--no-whole-file", "-I",
              "--size-only", "-u", "-k", "-K", "-L", "--copy-unsafe-links",
              "--safe-links", "--munge-links", "--delete", "--existing",
              "--ignore-existing", "--max-size=100000", "--compress-choice=zstd",
              "--no-inc-recursive", "--checksum-choice=md5"]


def _perturb(src, dest):
    """dest = an older/partial copy of src (so update/backup/delete/-u/-I bite)."""
    shutil.copytree(src, dest, symlinks=True)
    for p in sorted(dest.rglob("*")):
        if p.is_file() and not p.is_symlink():
            p.write_bytes(b"OLDER CONTENT\n")
            os.utime(p, (T_OLD, T_OLD))
            break
    (dest / "_obsolete").write_text("x\n")
    os.utime(dest / "_obsolete", (T_OLD, T_OLD))


def _fuzz_run(sndr, rcvr, transport, direction, wd, tag, opts, src):
    dest = wd / f"d_{tag}"
    _perturb(src, dest)
    if transport == "ssh":
        if direction == "push":
            rc, err, _, _, _ = _ssh1(sndr, rcvr, wd, opts, src, dest, pull=False)
        else:
            rc, err, _, _, _ = _ssh1(rcvr, sndr, wd, opts, src, dest, pull=True)
    else:  # daemon
        if direction == "push":
            rc, err, _, _, _ = _daemon1(sndr, rcvr, wd, dest, opts, src, pull=False)
        else:
            rc, err, _, _, _ = _daemon1(rcvr, sndr, wd, src, opts, dest, pull=True)
    return rc, err, snapshot(dest)


def run_fuzz(workroot, logf, n, seed):
    """Stochastic differential fuzzer: random fixture x option-subset x transport
    x direction x build-pair, each compared to the pure-B baseline for the same
    config. Finds cross-build / option-interaction regressions broadly."""
    import random
    rnd = random.Random(seed)
    A, B = RSYNC_A, RSYNC_B
    seen = set()
    nreg = nrun = 0
    print(f"fuzz: {n} iterations, seed={seed}")
    for i in range(n):
        fxname, fx = rnd.choice(FUZZ_FIXTURES)
        flags = sorted(rnd.sample(FUZZ_FLAGS, rnd.randint(0, 4)))
        opts = ["-a"] + flags
        transport = rnd.choice(["ssh", "daemon"])
        direction = rnd.choice(["push", "pull"])
        sndr, rcvr = rnd.choice([(A, A), (A, B), (B, A)])
        # skip option unsupported by either binary
        if any(o.startswith("--") and not (supports(A, o.split("=")[0])
               and supports(B, o.split("=")[0])) for o in opts):
            continue
        wd = workroot / f"fz{i}"
        shutil.rmtree(wd, ignore_errors=True)
        (wd / "src").mkdir(parents=True)
        fx(wd / "src")
        try:
            brc, berr, bsnap = _fuzz_run(B, B, transport, direction, wd, "base",
                                         opts, wd / "src")
            crc, cerr, csnap = _fuzz_run(sndr, rcvr, transport, direction, wd,
                                         "cfg", opts, wd / "src")
        except Exception as ex:
            continue
        nrun += 1
        bok = brc == 0 and not any(m in berr for m in ERR_MARKERS)
        cok = crc == 0 and not any(m in cerr for m in ERR_MARKERS)
        diffs = diff_snapshots(csnap, bsnap, ignore_mtime_types=_ign(opts))
        if not diffs and cok == bok:
            if not KEEP:
                shutil.rmtree(wd, ignore_errors=True)
            continue
        if cok and not bok:        # config better than pure-B
            if not KEEP:
                shutil.rmtree(wd, ignore_errors=True)
            continue
        which = f"sndr={'A' if sndr==A else 'B'} rcvr={'A' if rcvr==A else 'B'}"
        sig = (fxname, tuple(flags), transport, direction,
               "exit" if cok != bok else "tree")
        if sig in seen:
            if not KEEP:
                shutil.rmtree(wd, ignore_errors=True)
            continue
        seen.add(sig)
        nreg += 1
        label = f"fuzz:{fxname}/{transport}/{direction}/[{' '.join(opts)}]/{which}"
        print(f"DIFF  {label}")
        detail = ([f"  exit: cfg rc={crc}(ok={cok}) base rc={brc}(ok={bok})",
                   f"     err:{cerr[:200]}"] if cok != bok else []) + diffs[:8]
        for ln in detail:
            print(ln)
        logf.write(f"\nFUZZ-DIFF {label}  *** REGRESSION CANDIDATE ***\n")
        for ln in detail:
            logf.write(ln + "\n")
        logf.flush()
    print(f"\n=== fuzz: {nrun} configs run, {nreg} distinct regression candidates ===")
    return nreg


def _ign(opts):
    s = set()
    if "--omit-dir-times" in opts or any(o[:1] == "-" and o[1:2] != "-" and "O" in o for o in opts):
        s.add("d")
    if "--omit-link-times" in opts or any(o[:1] == "-" and o[1:2] != "-" and "J" in o for o in opts):
        s.add("l")
    if "--backup" in opts or any(o[:1] == "-" and o[1:2] != "-" and "b" in o for o in opts):
        s.add("d")
    # aux/implied dirs (temp/partial/backup, and --mkpath's created parents) sit
    # in the dest tree but get no source time -> their mtime is wall-clock and
    # differs between the A and B runs.
    if any(o.startswith(("--temp-dir", "--partial-dir", "--backup-dir",
                         "--partial", "--mkpath"))
           for o in opts):
        s.add("d")
    return s


# ---------------------------------------------------------------------------
# --loop: infinite scenario generators (random novel combos + systematic ladder)

# extra flags worth randomizing beyond the combo set (symlink / selection / wire)
_RAND_FLAGS = _COMBO_FLAGS + ["-k", "-K", "-L", "-l", "--copy-unsafe-links",
                              "--safe-links", "--munge-links", "--delete",
                              "--existing", "--ignore-existing",
                              "--no-inc-recursive", "--compress-choice=zstd",
                              "--checksum-choice=md5", "--sparse", "--fuzzy"]


def _random_scenarios(rnd, seen):
    """Infinite stream of randomized benign A/B scenarios: random fixture x random
    option subset (size 2-6), optional stale dest. Deduped by signature, fed
    through the same A/B oracle as the fixed sweeps."""
    idx = 0
    while True:
        fxname, fx = rnd.choice(FUZZ_FIXTURES)
        k = rnd.randint(2, 6)
        flags = tuple(sorted(rnd.sample(_RAND_FLAGS, k)))
        stale = fxname == "recvtree" and rnd.random() < 0.5
        sig = ("rand", fxname, flags, stale)
        if sig in seen:
            continue
        seen.add(sig)
        idx += 1
        nm = (f"rand{idx}:{fxname}:" + "_".join(f.lstrip("-") for f in flags)
              + ("+stale" if stale else ""))
        yield Scenario(nm, fx, ["-a", *flags], ["src/"], "dest/",
                       pre_dest=(stale_dest if stale else None))


def _systematic_combos(seen):
    """Infinite stream walking the option-combination ladder combo2->3->4->...
    over a stale dest; when the top order is exhausted the ladder restarts."""
    k = 2
    while True:
        for combo in itertools.combinations(_COMBO_FLAGS, k):
            sig = ("sys", combo)
            if sig in seen:
                continue
            seen.add(sig)
            yield Scenario(f"sys{k}:" + ",".join(combo), build_recvtree,
                           ["-a", *combo], ["src/"], "dest/", pre_dest=stale_dest)
        k += 1
        if k > len(_COMBO_FLAGS):       # exhausted every order -> restart ladder
            for s in [s for s in seen if s and s[0] == "sys"]:
                seen.discard(s)
            k = 2


def _mixed_scenarios(rnd, seen):
    """Alternate random / systematic so a parallel pool runs ~half of each."""
    rg = _random_scenarios(rnd, seen)
    sg = _systematic_combos(seen)
    while True:
        yield next(rg)
        yield next(sg)


class _Tee:
    """Minimal write/flush fan-out so matrix/fuzz diffs land in both the curated
    findings log and the full per-run log."""
    def __init__(self, *files):
        self._f = files

    def write(self, s):
        for f in self._f:
            f.write(s)

    def flush(self):
        for f in self._f:
            f.flush()


def main():
    global RSYNC_A, RSYNC_B, RRSYNC_A, RRSYNC_B, KEEP, REPEAT, CMD_TIMEOUT, COST, SCALE_N
    ap = argparse.ArgumentParser()
    ap.add_argument("--rsync-a", default="./rsync")
    ap.add_argument("--rsync-b", default="old_versions/rsync_3.4.1")
    ap.add_argument("--rrsync-a", default=None,
                    help="rrsync wrapper script paired with A for the rrsync lane "
                         "(default: in-tree support/rrsync)")
    ap.add_argument("--rrsync-b", default=None,
                    help="rrsync wrapper paired with B (e.g. a baseline version's "
                         "rrsync); rrsync regressions live in the script, so this "
                         "must match B's version to A/B the rrsync lane")
    ap.add_argument("--sweep", default="all",
                    choices=["options", "pathshape", "recv", "destshape",
                             "name", "filesfrom", "intree", "intree2", "proto",
                             "combo", "combo3", "combo4", "scale", "ssh",
                             "daemon", "daemonchroot", "mode", "size",
                             "filetype", "selection", "behavior", "placement",
                             "wire", "pairwise", "daemonsym", "daemonpull",
                             "daemonesc", "misc", "gaps", "redo", "typetrans",
                             "tsprec", "bigscale", "rrsync", "tcpdaemon",
                             "priv", "all"])
    ap.add_argument("--workdir", default="/tmp/abdiff")
    ap.add_argument("--findings", default="abdiff-findings.txt")
    ap.add_argument("--only", default=None, help="run only scenarios containing this substring")
    ap.add_argument("--keep", action="store_true")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--matrix", action="store_true",
                    help="run the cross-build role matrix (mixed A<->B, "
                         "push+pull, ssh+daemon) instead of the sweeps")
    ap.add_argument("--fuzz", type=int, default=0, metavar="N",
                    help="stochastic differential fuzzer: N random "
                         "fixture/option/transport/direction/version-pair configs")
    ap.add_argument("--seed", type=int, default=1, help="fuzz RNG seed")
    ap.add_argument("--repeat", type=int, default=REPEAT, metavar="N",
                    help="stability gate: run each binary N times per scenario; "
                         "scenarios whose own runs disagree are quarantined FLAKY "
                         "(default 2; use 1 to disable, 3+ to catch rarer flakes)")
    ap.add_argument("-j", "--jobs", type=int, default=20, metavar="N",
                    help="run N scenarios in parallel (default 20)")
    ap.add_argument("--loop", action="store_true",
                    help="after the fixed sweeps, keep generating new randomized + "
                         "higher-order-combo scenarios indefinitely (Ctrl-C to stop)")
    ap.add_argument("--cmd-timeout", type=int, default=CMD_TIMEOUT, metavar="SECS",
                    help="per-rsync wall-clock timeout; 0 disables (default 120)")
    ap.add_argument("--log-dir", default=".",
                    help="directory for the full per-run abdiff-log_<TIME>.txt "
                         "(default: current directory)")
    ap.add_argument("--cost", action="store_true",
                    help="also compare peak process-group RSS (resource oracle); "
                         "flags only gross, stable A-worse blow-ups. Pair with "
                         "--sweep bigscale --scale N.")
    ap.add_argument("--scale", type=int, default=SCALE_N, metavar="N",
                    help=f"element count for the bigscale fixtures (default {SCALE_N}; "
                         "use e.g. 100000 with --cost to surface footprint regressions)")
    ap.add_argument("--timelimit", type=float, default=0, metavar="SECS",
                    help="stop after SECS seconds (0 = no limit); in --loop it ends "
                         "the loop, in a finite sweep it stops queuing new scenarios. "
                         "In-flight scenarios finish, then a summary is written.")
    ap.add_argument("--root-extra", dest="root_extra", action="store_true",
                    default=None,
                    help="with --sweep all, also include the root-only sweeps "
                         "(priv, daemonchroot); auto-on when running as root")
    args = ap.parse_args()
    RSYNC_A = os.path.abspath(args.rsync_a)
    RSYNC_B = os.path.abspath(args.rsync_b)
    RRSYNC_A = os.path.abspath(args.rrsync_a) if args.rrsync_a else None
    RRSYNC_B = os.path.abspath(args.rrsync_b) if args.rrsync_b else None
    KEEP = args.keep
    REPEAT = args.repeat
    CMD_TIMEOUT = args.cmd_timeout
    COST = args.cost
    SCALE_N = args.scale

    if args.matrix or args.fuzz:
        workroot = Path(args.workdir)
        workroot.mkdir(parents=True, exist_ok=True)
        fp = Path(args.findings)
        fp.parent.mkdir(parents=True, exist_ok=True)
        logf = open(fp, "a", buffering=1)
        ld = Path(args.log_dir)
        ld.mkdir(parents=True, exist_ok=True)
        runlog_path = ld / f"abdiff-log_{time.strftime('%Y%m%d_%H%M%S')}.txt"
        runlog = open(runlog_path, "a", buffering=1)
        mode = "FUZZ" if args.fuzz else "MATRIX"
        header = (f"\n===== abdiff {mode} {time.strftime('%Y-%m-%d %H:%M:%S')} "
                  f"A={RSYNC_A} B={RSYNC_B} =====\n")
        logf.write(header)
        runlog.write(header)
        print(f"A (under test): {RSYNC_A}\nB (baseline): {RSYNC_B}\n"
              f"full log: {runlog_path}\n")
        tee = _Tee(logf, runlog)
        n = (run_fuzz(workroot, tee, args.fuzz, args.seed) if args.fuzz
             else run_matrix(workroot, tee))
        logf.close()
        runlog.close()
        return 1 if n else 0

    sweeps = ALL_SWEEPS if args.sweep == "all" else [args.sweep]
    # as root (or --root-extra), fold the root-only sweeps into an "all" run so a
    # privileged session exercises owner/device/specials/fake-super + chroot daemon.
    root_extra = args.root_extra if args.root_extra is not None else (os.geteuid() == 0)
    if args.sweep == "all" and root_extra:
        sweeps = sweeps + ["priv", "daemonchroot"]
        if COST:
            sweeps = sweeps + ["bigscale"]
    scns = []
    for s in sweeps:
        scns += SWEEPS[s]()
    if args.only:
        scns = [s for s in scns if args.only in s.name]
    if args.list:
        for s in scns:
            print(s.name, s.opts)
        return 0

    print(f"A (under test): {RSYNC_A}  ({sh([RSYNC_A,'--version']).stdout.splitlines()[0] if sh([RSYNC_A,'--version']).returncode==0 else '?'})")
    print(f"B (baseline):   {RSYNC_B}  ({sh([RSYNC_B,'--version']).stdout.splitlines()[0]})")

    # absolute: scenarios run rsync with cwd=workdir and also pass dest/aux paths
    # under it, so a relative --workdir would double-resolve (rsync writes to
    # wd/wd/...) and every scenario would go silently vacuous.
    workroot = Path(os.path.abspath(args.workdir))
    workroot.mkdir(parents=True, exist_ok=True)
    # Curated, cross-run findings log: open once, write a run header, and flush
    # each anomaly the moment it is found so the log is a live record mid-run.
    fp = Path(args.findings)
    fp.parent.mkdir(parents=True, exist_ok=True)
    logf = open(fp, "a", buffering=1)  # line-buffered
    logf.write(f"\n===== abdiff run {time.strftime('%Y-%m-%d %H:%M:%S')} "
               f"sweep={args.sweep} A={RSYNC_A} B={RSYNC_B} jobs={args.jobs}"
               f"{' loop' if args.loop else ''} =====\n")
    logf.flush()
    # Per-run findings log: fresh timestamped file in the current dir (or
    # --log-dir). Holds ONLY findings (DIFF/TIMEOUT/ERROR/FLAKY/ALLOW/BETTER) --
    # no OK/SKIP noise; stdout shows a live "test N" counter instead.
    ld = Path(args.log_dir)
    ld.mkdir(parents=True, exist_ok=True)
    runlog_path = ld / f"abdiff-log_{time.strftime('%Y%m%d_%H%M%S')}.txt"
    runlog = open(runlog_path, "a", buffering=1)
    runlog.write(f"# abdiff findings  {time.strftime('%Y-%m-%d %H:%M:%S')}\n"
                 f"# A={RSYNC_A}\n# B={RSYNC_B}\n"
                 f"# sweep={args.sweep} jobs={args.jobs} repeat={REPEAT} "
                 f"loop={args.loop} cmd_timeout={CMD_TIMEOUT}\n")
    runlog.flush()
    print(f"findings log: {runlog_path}\njobs: {args.jobs}"
          f"{'  (loop: Ctrl-C to stop)' if args.loop else ''}")

    def ostr(s):  # opts may be callable (placement scenarios)
        return ' '.join(s.opts) if not callable(s.opts) else '(dynamic)'

    LABEL = {"OK": "OK   ", "SKIP": "SKIP ", "FLAKY": "FLAKY", "ABETTER": "BETTER",
             "ALLOW": "ALLOW", "DIFF": "DIFF ", "TIMEOUT": "TIME ", "ERROR": "ERROR"}
    counts = {k: 0 for k in LABEL}
    done = [0]
    rec_lock = threading.Lock()

    def progress(total=None):
        sys.stdout.write(f"test {done[0]}{('/' + str(total)) if total else ''}"
                         f"{('  ' + str(counts['DIFF']) + ' DIFF') if counts['DIFF'] else ''}\r")
        sys.stdout.flush()

    def record(s, status, info, total=None):
        if isinstance(info, str):
            info = [info]
        # a subprocess timeout surfaces as a DIFF whose err carries the marker;
        # promote it to its own TIMEOUT class so it's easy to triage.
        if status == "DIFF" and any("[abdiff: TIMEOUT]" in ln for ln in info):
            status = "TIMEOUT"
        with rec_lock:
            counts[status] = counts.get(status, 0) + 1
            done[0] += 1
            if status not in ("OK", "SKIP"):   # a finding: print it + log it
                sys.stdout.write("\r")          # clear the progress line
                print(f"{LABEL.get(status, status)} {s.name}  [#{done[0]}]")
                for line in info:
                    print(line)
                suffix = (" *** REGRESSION CANDIDATE ***"
                          if status in ("DIFF", "TIMEOUT", "ERROR") else "")
                for f in (runlog, logf):
                    f.write(f"\n{status} {s.name}  opts: {ostr(s)}{suffix}\n")
                    for line in info:
                        f.write(line + "\n")
                    f.flush()
            progress(total)

    def worker(s):
        try:
            return run_scenario(s, workroot)
        except Exception as ex:        # never let one scenario kill the pool
            return ("ERROR", [f"  exception: {ex!r}"])

    def summary_line(prefix):
        return (f"{prefix} {counts['OK']} OK, {counts['SKIP']} skipped, "
                f"{counts['FLAKY']} FLAKY, {counts['ALLOW']} ALLOW (intentional), "
                f"{counts['ABETTER']} BETTER (A>B), {counts['TIMEOUT']} TIMEOUT, "
                f"{counts['ERROR']} ERROR, {counts['DIFF']} DIFF "
                f"(regression candidates) ===")

    deadline = (time.time() + args.timelimit) if args.timelimit else None
    if deadline:
        print(f"time limit: {args.timelimit:.0f}s\n")

    if not args.loop:
        total = len(scns)
        print(f"scenarios: {total}\n")
        with ThreadPoolExecutor(max_workers=args.jobs) as ex:
            futs = {ex.submit(worker, s): s for s in scns}
            for fut in as_completed(futs):
                status, info = fut.result()
                record(futs[fut], status, info, total=total)
                if deadline and time.time() > deadline:
                    print("\n[time limit reached -- cancelling pending scenarios]")
                    for p in futs:
                        p.cancel()
                    break
        summary = summary_line("===")
        print("\n" + summary)
        runlog.write("\n" + summary + "\n")
        logf.write(summary + "\n")
        runlog.close()
        logf.close()
        return 1 if (counts['DIFF'] or counts['TIMEOUT'] or counts['ERROR']) else 0

    # --loop: run the fixed sweeps first, then an endless mixed stream of new
    # randomized + systematic-combo scenarios, keeping the pool full.
    rnd = random.Random(args.seed)
    seen = set()
    mixed = _mixed_scenarios(rnd, seen)
    fixed = iter(scns)

    def next_scn():
        try:
            return next(fixed)
        except StopIteration:
            return next(mixed)

    ex = ThreadPoolExecutor(max_workers=args.jobs)
    inflight = {}

    def submit_one():
        s = next_scn()
        inflight[ex.submit(worker, s)] = s

    try:
        for _ in range(args.jobs * 2):
            submit_one()
        while True:
            fdone, _pending = wait(list(inflight), return_when=FIRST_COMPLETED)
            for fut in fdone:
                s = inflight.pop(fut)
                status, info = fut.result()
                record(s, status, info)
                if not (deadline and time.time() > deadline):
                    submit_one()
            if deadline and time.time() > deadline and not inflight:
                print("\n[time limit reached -- writing summary]")
                break
    except KeyboardInterrupt:
        print("\n[interrupted -- cancelling pending, writing summary]")
        ex.shutdown(wait=False, cancel_futures=True)
    finally:
        summary = summary_line("=== loop stopped:")
        print("\n" + summary)
        runlog.write("\n" + summary + "\n")
        runlog.flush()
        logf.write(summary + "\n")
        logf.flush()
        runlog.close()
        logf.close()
    return 1 if counts['DIFF'] else 0


if __name__ == "__main__":
    sys.exit(main())

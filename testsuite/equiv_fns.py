"""Transport-equivalence harness for rsync tests.

This module runs a single transfer *scenario* across rsync's four transports
and structurally diffs the resulting destination trees, partitioning every
difference into "must be byte-equal" vs "may differ only by a documented
mapping" (uid/gid/ACL-id/xattr-namespace, tolerated when unprivileged).

The four transports:

  * ``local``       -- a plain local rsync (src and dst are both paths).
  * ``ssh``         -- a remote-shell transfer via support/lsh.sh
                       (``-e lsh localhost:DEST`` with ``--rsync-path``).
  * ``daemon_pipe`` -- an rsync:// daemon reached over a private stdio pipe
                       (RSYNC_CONNECT_PROG; opens no listening socket).
  * ``daemon_tcp``  -- an rsync:// daemon bound to a real 127.0.0.1 socket.
                       Only runs under ``--use-tcp`` (see ``require_tcp``);
                       degrades to a clean skip otherwise.

Black-box driving
-----------------
Every transport is driven through a single ``rsync_bin`` parameter that
defaults to the RSYNC env command but accepts an arbitrary rsync binary
path. The daemon side is launched from the *same* ``rsync_bin``, so the
whole matrix can be pointed at, e.g., a v3.4.2 vs a v3.4.3 binary without
touching the comparison logic. This is what the later proof-of-oracle uses
to demonstrate that #915 regressed link-dest-over-daemon between releases.

The comparison logic (``capture_tree`` / ``diff_trees`` / ``partition_diffs``)
never sees the binary; it works purely off the on-disk result, so it is
independent of how the binary under test was built.
"""

from __future__ import annotations

import os
import shlex
import stat
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

import rsyncfns
from rsyncfns import (
    RSYNC, SCRATCHDIR, SRCDIR,
    claim_ports, require_tcp, rmtree, test_fail, write_daemon_conf,
)


LSH = str(SRCDIR / 'support' / 'lsh.sh')

# The four transports we assert equivalence across. ``daemon_tcp`` is the
# only one that needs a real listening socket; the rest work under plain
# ``make check``.
TRANSPORTS = ('local', 'ssh', 'daemon_pipe', 'daemon_tcp')


# --------------------------------------------------------------------------
# Privilege model
# --------------------------------------------------------------------------

def am_root() -> bool:
    return os.geteuid() == 0


def numeric_ids_only() -> bool:
    """True when we can expect uid/gid to be reproduced verbatim.

    Owner is only preserved when running as root (``-o`` is a no-op for an
    unprivileged client, and a non-root daemon cannot set uid/gid at all --
    write_daemon_conf() comments out the uid/gid lines off-root). When this
    is False, an owner/group divergence is the *documented mapping*, not a
    defect, and partition_diffs() tolerates it.
    """
    return am_root()


# --------------------------------------------------------------------------
# Black-box rsync invocation (independent of the RSYNC env binary)
# --------------------------------------------------------------------------

def _bin_argv(rsync_bin: str) -> list:
    """argv prefix for an arbitrary rsync binary command.

    ``rsync_bin`` may itself be multi-word (e.g. 'valgrind ... rsync' or a
    binary plus '--protocol=N'); shlex-split it so subprocess gets a real
    argv. Defaults are handled by the callers passing RSYNC.
    """
    return shlex.split(rsync_bin)


@dataclass
class RunResult:
    transport: str
    returncode: int
    stdout: str
    stderr: str
    argv: list


# --------------------------------------------------------------------------
# Per-transport daemon plumbing, parametrized by rsync_bin
# --------------------------------------------------------------------------

def _start_daemon_for_bin(rsync_bin: str, conf_path: Path, port: int,
                          *, use_tcp: bool) -> str:
    """Bring up a daemon running ``rsync_bin`` and return the URL prefix.

    Mirrors rsyncfns.start_test_daemon but launches the *given* binary so an
    external rsync can be driven black-box. In pipe mode this sets
    RSYNC_CONNECT_PROG (no socket); in TCP mode it spawns a real loopback
    rsyncd via the rsyncfns kill-on-exit machinery.
    """
    if use_tcp:
        # A prior daemon_pipe leg in the same process sets RSYNC_CONNECT_PROG,
        # which would hijack this TCP client into the stale pipe daemon (the
        # client prefers the connect prog over a real socket). Clear it so the
        # TCP leg actually uses the bound socket.
        os.environ.pop('RSYNC_CONNECT_PROG', None)
        claim_ports(port)
        argv = _bin_argv(rsync_bin) + [
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
            preexec_fn=rsyncfns._set_pdeathsig,
        )
        import atexit
        atexit.register(rsyncfns._stop_rsyncd, proc)

        deadline = time.monotonic() + 10
        last_err = None
        import socket as _socket
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                test_fail(f"daemon ({rsync_bin}) exited before listening on "
                          f"port {port} (status={proc.returncode})")
            try:
                with _socket.create_connection(('127.0.0.1', port), timeout=0.5):
                    return f'rsync://localhost:{port}/'
            except OSError as e:
                last_err = e
                time.sleep(0.05)
        rsyncfns._stop_rsyncd(proc)
        test_fail(f"daemon ({rsync_bin}) never listened on 127.0.0.1:{port}: "
                  f"{last_err}")
    # Pipe mode: the client forks the daemon over a private stdio pipe.
    os.environ['RSYNC_CONNECT_PROG'] = f'{rsync_bin} --config={conf_path} --daemon'
    return 'rsync://localhost/'


# --------------------------------------------------------------------------
# Scenario runner
# --------------------------------------------------------------------------

@dataclass
class Scenario:
    """A transfer to replay across transports.

    ``opts``    : rsync options (NOT including src/dst), e.g. ['-aH', '--delete'].
    ``rel_src`` : source path relative to the per-transport work dir; trailing
                  slash semantics are preserved.
    ``rel_dst`` : destination path relative to the per-transport work dir.
    ``setup``   : callable(workdir: Path) -> None, populates the work dir
                  (source tree, any link-dest/compare-dest basis dirs, an
                  existing destination to be --deleted into, etc.). Run fresh
                  per transport so each leg starts from identical inputs.
    ``module_subpath`` : for daemon transports, the path *inside* the module
                  that maps to ``rel_dst`` (the module is rooted at the work
                  dir). Defaults to ``rel_dst``.
    ``expect_returncode`` : the rsync exit status this scenario is expected to
                  produce on EVERY transport leg. Defaults to 0. Scenarios that
                  legitimately partial-transfer (e.g. ``--delete`` racing a
                  vanished source, or some ``--link-dest`` setups) set this to
                  23 explicitly so a *new* partial-transfer regression in a
                  scenario that should return 0 is no longer silently tolerated.
    """
    opts: list
    rel_src: str
    rel_dst: str
    setup: object
    extra_opts_for: dict = field(default_factory=dict)
    expect_returncode: int = 0


def _work_dir(transport: str) -> Path:
    d = SCRATCHDIR / f'equiv-{transport}'
    rmtree(d)
    d.mkdir(parents=True)
    return d


def _join_slash(base: Path, rel: str) -> str:
    """Join base/rel preserving a trailing slash on rel (rsync-significant)."""
    s = str(base / rel)
    if rel.endswith('/') and not s.endswith('/'):
        s += '/'
    return s


def run_scenario(scenario: Scenario, transport: str, *,
                 rsync_bin: str = None, port: int = 12890) -> tuple:
    """Run ``scenario`` over ``transport`` against ``rsync_bin``.

    Returns ``(dest_dir: Path, RunResult)``. The destination tree is left on
    disk for capture_tree(). ``rsync_bin`` defaults to the RSYNC env command.

    For ``daemon_tcp`` the caller is responsible for having gated on
    require_tcp(); this function will still skip the leg cleanly if invoked
    without --use-tcp by raising SkipLeg.
    """
    if rsync_bin is None:
        rsync_bin = RSYNC

    work = _work_dir(transport)
    scenario.setup(work)

    src = _join_slash(work, scenario.rel_src)
    dst_dir = work / scenario.rel_dst.rstrip('/')
    extra = scenario.extra_opts_for.get(transport, [])
    base_argv = _bin_argv(rsync_bin) + list(scenario.opts) + list(extra)

    if transport == 'local':
        dst = _join_slash(work, scenario.rel_dst)
        argv = base_argv + [src, dst]

    elif transport == 'ssh':
        dst = _join_slash(work, scenario.rel_dst)
        argv = base_argv + ['-e', LSH, f'--rsync-path={rsync_bin}',
                            src, f'localhost:{dst}']

    elif transport in ('daemon_pipe', 'daemon_tcp'):
        use_tcp = (transport == 'daemon_tcp')
        if use_tcp and not rsyncfns.USE_TCP:
            raise SkipLeg('daemon_tcp needs --use-tcp')
        # Module rooted at the work dir; the daemon writes into it via a
        # module-relative path. read-only=no so a push can land.
        conf = write_daemon_conf(
            [('equiv', {'path': str(work), 'read only': 'no'})],
            name=f'equiv-{transport}.conf',
        )
        prefix = _start_daemon_for_bin(rsync_bin, conf, port, use_tcp=use_tcp)
        module_dst = scenario.rel_dst
        url = f'{prefix}equiv/{module_dst}'
        argv = base_argv + [src, url]
    else:
        raise ValueError(f'unknown transport {transport!r}')

    proc = subprocess.run(argv, capture_output=True, text=True)
    return dst_dir, RunResult(transport, proc.returncode,
                              proc.stdout, proc.stderr, argv)


class SkipLeg(Exception):
    """Raised to skip a single transport leg (e.g. TCP without --use-tcp)."""


# --------------------------------------------------------------------------
# Structural tree capture
# --------------------------------------------------------------------------

@dataclass
class FileEntry:
    path: str            # relative path within the captured root
    ftype: str           # 'file' | 'dir' | 'symlink' | 'other'
    size: int
    mode: int            # st_mode & 0o7777
    mtime: int           # whole seconds
    mtime_nsec: int      # nanosecond remainder
    linktarget: str      # symlink target, or '' for non-symlinks
    uid: int
    gid: int
    content_sha: str     # sha256 of regular-file content, or '' otherwise
    ino_key: int         # (dev, ino) collapsed into a group id; see capture_tree


@dataclass
class Tree:
    root: Path
    entries: dict                       # path -> FileEntry
    ino_groups: dict                    # ino_key -> sorted list of paths
    deletion_set: set = field(default_factory=set)


def _sha256(path: Path) -> str:
    import hashlib
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 16), b''):
            h.update(chunk)
    return h.hexdigest()


def capture_tree(root) -> Tree:
    """Walk ``root`` with os.lstat/os.scandir and record per-file structure.

    Inode grouping: files that share a (st_dev, st_ino) get the same
    ``ino_key`` and are listed together in ``ino_groups`` -- this is what the
    hardlink / link-dest assertions read. We key on (dev, ino) so a link-dest
    that lands on a different filesystem can never be mistaken for a shared
    inode.
    """
    root = Path(root)
    entries: dict = {}
    devino_to_group: dict = {}
    ino_groups: dict = {}
    next_group = [0]

    def group_for(st) -> int:
        key = (st.st_dev, st.st_ino)
        if key not in devino_to_group:
            devino_to_group[key] = next_group[0]
            next_group[0] += 1
        return devino_to_group[key]

    def walk(d: Path, relbase: str):
        with os.scandir(d) as it:
            for de in sorted(it, key=lambda e: e.name):
                rel = de.name if not relbase else f'{relbase}/{de.name}'
                st = os.lstat(de.path)
                m = st.st_mode
                if stat.S_ISDIR(m):
                    ftype, target, content = 'dir', '', ''
                    entries[rel] = _entry(rel, ftype, st, target, content,
                                          group_for(st))
                    walk(Path(de.path), rel)
                elif stat.S_ISLNK(m):
                    target = os.readlink(de.path)
                    entries[rel] = _entry(rel, 'symlink', st, target, '',
                                          group_for(st))
                elif stat.S_ISREG(m):
                    content = _sha256(Path(de.path))
                    entries[rel] = _entry(rel, 'file', st, '', content,
                                          group_for(st))
                else:
                    entries[rel] = _entry(rel, 'other', st, '', '',
                                          group_for(st))

    if root.exists():
        walk(root, '')

    for rel, e in entries.items():
        ino_groups.setdefault(e.ino_key, []).append(rel)
    for k in ino_groups:
        ino_groups[k].sort()

    return Tree(root=root, entries=entries, ino_groups=ino_groups)


def _entry(rel, ftype, st, target, content, group) -> FileEntry:
    nsec = getattr(st, 'st_mtime_ns', int(st.st_mtime * 1e9)) % 1_000_000_000
    return FileEntry(
        path=rel,
        ftype=ftype,
        size=st.st_size if ftype != 'dir' else 0,
        mode=stat.S_IMODE(st.st_mode),
        mtime=int(st.st_mtime),
        mtime_nsec=nsec,
        linktarget=target,
        uid=st.st_uid,
        gid=st.st_gid,
        content_sha=content,
        ino_key=group,
    )


# --------------------------------------------------------------------------
# Structural diff + partition
# --------------------------------------------------------------------------

# Fields that must be byte-equal across every transport.
STRICT_FIELDS = ('ftype', 'size', 'mode', 'mtime', 'mtime_nsec',
                 'linktarget', 'content_sha')
# Fields that may legitimately differ by a documented mapping when
# unprivileged (asserted equal only when numeric_ids_only()).
MAPPED_FIELDS = ('uid', 'gid')


@dataclass
class Diff:
    path: str
    field: str
    a: object
    b: object


def diff_trees(a: Tree, b: Tree) -> dict:
    """Compare two captured trees field by field.

    Returns a dict with:
      'only_in_a' / 'only_in_b' : paths present in exactly one tree
      'strict'                  : list[Diff] over STRICT_FIELDS
      'mapped'                  : list[Diff] over MAPPED_FIELDS
      'ino_group_mismatch'      : list of human-readable strings where the
                                  inode-grouping partition differs
    """
    out = {
        'only_in_a': sorted(set(a.entries) - set(b.entries)),
        'only_in_b': sorted(set(b.entries) - set(a.entries)),
        'strict': [],
        'mapped': [],
        'ino_group_mismatch': [],
    }
    for path in sorted(set(a.entries) & set(b.entries)):
        ea, eb = a.entries[path], b.entries[path]
        for f in STRICT_FIELDS:
            va, vb = getattr(ea, f), getattr(eb, f)
            if va == vb:
                continue
            # Directory mtime nanoseconds: rsync's default --modify-window=0
            # compares directory times at whole-second granularity (rsync.1.md),
            # so the nanosecond remainder of a directory's mtime is not
            # preserved unless -@ -1 / --modify-window=-1 is given to enable
            # strict sub-second comparison. Additionally, protocol-30 drops
            # nsec in the wire encoding for directories. The whole-second dir
            # mtime is still strict; only the nsec remainder goes to 'mapped'.
            if f == 'mtime_nsec' and ea.ftype == 'dir':
                out['mapped'].append(Diff(path, 'dir_mtime_nsec', va, vb))
                continue
            out['strict'].append(Diff(path, f, va, vb))
        for f in MAPPED_FIELDS:
            va, vb = getattr(ea, f), getattr(eb, f)
            if va != vb:
                out['mapped'].append(Diff(path, f, va, vb))

    # Inode grouping equivalence: build, for each tree, the partition of
    # shared-inode sets (groups of size > 1 are the meaningful ones).
    def shared_sets(t: Tree) -> set:
        return frozenset(
            frozenset(paths) for paths in t.ino_groups.values()
            if len(paths) > 1
        )
    sa, sb = shared_sets(a), shared_sets(b)
    if sa != sb:
        out['ino_group_mismatch'].append(
            f'shared-inode partitions differ: {sorted(map(sorted, sa))} '
            f'!= {sorted(map(sorted, sb))}'
        )
    return out


def partition_diffs(diff: dict) -> tuple:
    """Split a diff_trees() result into (fatal, tolerated) lists of strings.

    Fatal:
      * any membership difference (only_in_a / only_in_b) -- the deletion set
        and file set must match across transports;
      * any STRICT_FIELDS difference;
      * any inode-grouping mismatch.
    Tolerated (only when NOT numeric_ids_only()):
      * uid/gid differences, the documented owner-mapping divergence on an
        unprivileged daemon. When running as root these are promoted to fatal.
    """
    fatal, tolerated = [], []
    for p in diff['only_in_a']:
        fatal.append(f'present only in A: {p}')
    for p in diff['only_in_b']:
        fatal.append(f'present only in B: {p}')
    for d in diff['strict']:
        fatal.append(f'{d.path}: {d.field} {d.a!r} != {d.b!r}')
    for d in diff['ino_group_mismatch']:
        fatal.append(d)
    for d in diff['mapped']:
        msg = f'{d.path}: {d.field} {d.a!r} != {d.b!r}'
        if d.field in MAPPED_FIELDS:
            # uid/gid: the owner mapping. Tolerated only when unprivileged;
            # promoted to fatal under root, where owner MUST be preserved.
            if numeric_ids_only():
                fatal.append(msg + ' (root: owner must be preserved)')
            else:
                tolerated.append(msg + ' (unprivileged owner-mapping, tolerated)')
        else:
            # Non-owner documented mapping (e.g. directory mtime nsec): always
            # tolerated, never owner-dependent.
            tolerated.append(msg + ' (documented transport non-equivalence)')
    return fatal, tolerated


# --------------------------------------------------------------------------
# High-level equivalence assertion
# --------------------------------------------------------------------------

def run_matrix(scenario: Scenario, *, rsync_bin: str = None,
               port: int = 12890, transports=TRANSPORTS) -> dict:
    """Run ``scenario`` across all transports, returning {transport: Tree}.

    Legs that cannot run (daemon_tcp without --use-tcp) are recorded as None
    and skipped, not failed.
    """
    trees: dict = {}
    for t in transports:
        try:
            dst_dir, res = run_scenario(scenario, t, rsync_bin=rsync_bin,
                                        port=port)
        except SkipLeg:
            trees[t] = None
            continue
        if res.returncode != scenario.expect_returncode:
            test_fail(f'[{t}] rsync exited {res.returncode}, expected '
                      f'{scenario.expect_returncode}: '
                      f'{" ".join(res.argv)}\n{res.stderr}')
        trees[t] = capture_tree(dst_dir)
    return trees


def assert_equivalent(trees: dict, *, reference: str = 'local') -> list:
    """Assert all present trees are equivalent to the reference transport.

    Fatal diffs call test_fail(). Returns the list of tolerated-diff strings
    (for the caller to log). Skipped legs (None) are ignored.
    """
    if trees.get(reference) is None:
        # Reference itself skipped (shouldn't happen for 'local'); pick the
        # first present tree as reference.
        present = [t for t, v in trees.items() if v is not None]
        if not present:
            test_fail('no transport legs ran')
        reference = present[0]

    ref_tree = trees[reference]
    all_tolerated: list = []
    for t, tree in trees.items():
        if tree is None or t == reference:
            continue
        diff = diff_trees(ref_tree, tree)
        fatal, tolerated = partition_diffs(diff)
        all_tolerated += [f'[{reference} vs {t}] {m}' for m in tolerated]
        if fatal:
            detail = '\n  '.join(fatal)
            test_fail(f'transport divergence {reference} vs {t}:\n  {detail}')
    return all_tolerated

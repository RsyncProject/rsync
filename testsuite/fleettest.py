#!/usr/bin/env python3
"""Fleet CI harness for rsync.

Builds the committed HEAD of an rsync checkout on a fleet of remote machines
(over ssh), runs the test suite under both transports (default stdio-pipe and
--use-tcp) in parallel, and prints one report of only the UNEXPECTED results --
a fast local pre-flight for the GitHub CI matrix.

Each target maps 1:1 to a .github/workflows/*.yml job: the per-target configure
flags mirror that workflow, and the pipe-run RSYNC_EXPECT_SKIPPED list is PARSED
from the workflow (not hardcoded). The --use-tcp run never sets an expected-skip
list (matching the workflows), so only test FAILs matter there.

A target may also list older "protocols" (e.g. [30, 29]) in the fleet config:
each runs as an extra stdio-pipe pass with runtests --protocol=N (the fleet
analogue of a workflow's check30/check29 steps), using the same parsed skip list
as the pipe run, and shows up as a protoNN column in the report.

The fleet -- which machines, how to reach and build each -- is read from a JSON
config: ~/.fleettest.json if present, else fleettest.json next to this script,
or --fleet PATH. Copy the bundled fleettest.json.example to either location (or
symlink it) and edit for your own hosts; see testsuite/README.md and the
comments in fleettest.json.example.

Source = `git archive HEAD` of the rsync tree (the current directory, or --repo
PATH) -- source-only, no .o/binaries are ever pushed.

Every run uses its own randomly-named build directory on each target
(<builddir>-<run_id>), so two or three fleettest runs can share the same fleet
without interfering: each pushes, builds and tests in isolation. The run dir is
removed when the run ends -- on success or failure, and best-effort on
Ctrl-C/kill (pass --keep to retain it for inspection). A run that is hard-killed
(SIGKILL), or signalled mid-push, or whose ssh dies during cleanup can leave a
stray <builddir>-<id> behind; sweep those with `fleettest.py --cleanup`
(optionally scoped with --targets). Because each
run starts from a fresh dir, every build is a full configure + build.

PROVISIONING: each target must have the build toolchain its workflow's prepare
step installs -- the target regenerates its own configure/proto.h/man pages, so
it needs autoconf+automake, perl, a python3 markdown lib (cmarkgfm or commonmark)
unless its flags pass --disable-md2man, and the dev libraries for whatever its
configure flags enable (e.g. --with-rrsync needs openssl/xxhash/zstd/lz4 headers).
A missing piece shows up as BUILD-FAIL with configure's own "you need X" hint.

Per-target "privilege" (set in the JSON) controls how the suite runs: "root"
(already root -- run directly), "sudo" (build unprivileged, run the suite via
sudo to match a CI runner), or "user" (run directly as a plain non-root user). A
target with "nonroot": true additionally reruns -- as the (non-root) ssh user,
after the sudo runs -- every test that declares `fleet_nonroot = True` at module
level, so privilege-sensitive tests opt in from the test file itself with no
fleet-config edit when new ones are added.

Usage (run from inside an rsync checkout, or pass --repo):
    python3 testsuite/fleettest.py                 # whole fleet, both transports
    python3 testsuite/fleettest.py --targets cygwin,freebsd
    python3 testsuite/fleettest.py --transport pipe
    python3 testsuite/fleettest.py --keep          # keep run dirs for inspection
    python3 testsuite/fleettest.py --cleanup       # sweep stray run dirs, exit
    python3 testsuite/fleettest.py --fleet my-fleet.json --list

Exit 0 iff every selected (target x transport) cell is OK.
"""

from __future__ import annotations

import argparse
import atexit
import concurrent.futures
import dataclasses
import json
import os
import re
import secrets
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

# Set from --repo in main() (default: cwd). The harness builds whatever rsync
# source tree these point at, so it must be run from inside an rsync checkout
# or given --repo PATH.
REPO = Path.cwd()
WORKFLOWS = REPO / ".github" / "workflows"

# Fleet config (overridable with --fleet): ~/.fleettest.json is tried first, then
# fleettest.json next to this script. The example template sits next to the
# script too.
HOME_CONFIG = Path.home() / ".fleettest.json"
SCRIPT_CONFIG = Path(__file__).resolve().parent / "fleettest.json"
DEFAULT_CONFIGS = [HOME_CONFIG, SCRIPT_CONFIG]
EXAMPLE_CONFIG = SCRIPT_CONFIG.with_name(SCRIPT_CONFIG.name + ".example")

# The pushed tree is source-only (git archive). Each target regenerates its own
# build files, so --delete must NOT prune them: we exclude everything `make`
# produces (autotools output, proto.h, man pages, config.h/Makefile, *.o, the
# binaries) plus test artifacts a prior sudo run left root-owned (testtmp,
# __pycache__, *.pyc -- which a non-root --delete can't unlink). Excluded paths
# are protected from --delete, so each target keeps its native build state for
# incremental rebuilds. `configure` itself is committed, so it is NOT excluded.
PUSH_EXCLUDES = [
    ".git", "config.h", "config.status", "config.log", "Makefile", "shconfig",
    "configure.sh", "config.h.in", "aclocal.m4", "proto.h", "git-version.h",
    "/rsync.1", "/rsync-ssl.1", "/rsyncd.conf.5", "/rrsync.1",
    "*.o", "*.exe", "__pycache__", "*.pyc", "/testtmp",
    "/rsync", "/tls", "/getgroups", "/getfsdev", "/trimslash", "/wildtest",
    "/testrun", "/simdtest", "/t_unsafe", "/t_chmod_secure", "/t_rename_secure",
    "/t_symlink_secure", "/t_secure_relpath",
]


@dataclasses.dataclass
class Target:
    name: str
    ssh_host: str | None          # null in JSON => run locally
    workflow: str                 # filename under .github/workflows
    configure_flags: list[str]
    make: str = "make"            # e.g. "gmake" on the BSDs/Solaris
    env_prefix: str = ""          # exported before configure AND make (e.g. PATH)
    configure_pre: str = ""       # shell run before ./configure (env exports, brew)
    python: str = "python3"
    rsync_bin: str = "rsync"      # "rsync.exe" on Cygwin
    privilege: str = "root"       # "root" (already root) | "sudo" | "user" (plain, no sudo)
    pipe_jobs: int = 8
    tcp_jobs: int = 8
    # Base build-dir name (relative to remote $HOME; absolute for local). A
    # per-run random suffix is appended (-> <builddir>-<run_id>) so concurrent
    # fleettest runs don't share a tree; --cleanup sweeps leftover <builddir>-*.
    builddir: str = "rsync-citest"
    # When true, after the sudo runs, additionally run -- as the (non-root) ssh
    # user -- every test that declares `fleet_nonroot = True` (see
    # discover_nonroot_tests). Mirrors a workflow's non-root check step.
    nonroot: bool = False
    # Older protocol versions to additionally exercise, each as a separate
    # stdio-pipe pass with runtests --protocol=N (the fleet analogue of a
    # workflow's check30/check29 steps). e.g. [30, 29]. Empty => proto pass off.
    protocols: list[int] = dataclasses.field(default_factory=list)
    # Per-target retry budget for FLAKY tests: after a run, each failed test is
    # re-run on its own up to max_retry more times, and any that then pass are
    # dropped from the failure list (and reported as "recovered", never hidden).
    # Use on a slow/loaded box where concurrency-sensitive tests occasionally
    # flake, instead of dropping the whole target to a lower -j. 0 => no retry.
    max_retry: int = 0


def load_fleet(path: Path) -> list[Target]:
    """Load the fleet from a JSON file of the shape {"targets": [ {...}, ... ]}.

    Each entry's keys are Target fields; keys starting with "_" are treated as
    comments and ignored (both at top level and per target). Validation errors
    name the offending target so a typo is easy to find."""
    try:
        data = json.loads(path.read_text())
    except OSError as e:
        sys.exit(f"cannot read fleet config {path}: {e}")
    except json.JSONDecodeError as e:
        sys.exit(f"invalid JSON in {path}: {e}")
    if not isinstance(data, dict) or not isinstance(data.get("targets"), list):
        sys.exit(f'{path}: expected a JSON object with a "targets" array')
    fields = {f.name for f in dataclasses.fields(Target)}
    fleet: list[Target] = []
    for i, entry in enumerate(data["targets"]):
        if not isinstance(entry, dict):
            sys.exit(f"{path}: targets[{i}] is not an object")
        entry = {k: v for k, v in entry.items() if not k.startswith("_")}
        who = entry.get("name", f"targets[{i}]")
        bad = set(entry) - fields
        if bad:
            sys.exit(f"{path}: target {who!r} has unknown key(s): "
                     f"{', '.join(sorted(bad))}")
        try:
            fleet.append(Target(**entry))
        except TypeError as e:
            sys.exit(f"{path}: target {who!r}: {e}")
    if not fleet:
        sys.exit(f"{path}: no targets defined")
    return fleet


# ---------------------------------------------------------------------------
# command execution (ssh for remote, local shell when ssh_host is null)
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class CmdResult:
    rc: int
    out: str          # combined stdout + stderr
    timed_out: bool = False


def run_on(target: Target, script: str, timeout: int) -> CmdResult:
    """Run a /bin/sh script on the target. Remote via ssh, else local."""
    if target.ssh_host:
        argv = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15",
                target.ssh_host, script]
    else:
        argv = ["/bin/sh", "-c", script]
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        return CmdResult(p.returncode, (p.stdout or "") + (p.stderr or ""))
    except subprocess.TimeoutExpired as e:
        out = (e.stdout or b"") + (e.stderr or b"")
        if isinstance(out, bytes):
            out = out.decode(errors="replace")
        return CmdResult(124, out, timed_out=True)
    except FileNotFoundError as e:
        return CmdResult(127, str(e))


def push_argv(target: Target, staging: str) -> list[str]:
    # -rlpgoD = -a without -t: do NOT preserve mtimes. The host clock can be
    # hours AHEAD of a target, so preserved (commit-time) mtimes land "in the
    # future" there and rsync's `Makefile: Makefile.in config.status` rule
    # triggers a config.status/autoconf regeneration storm. Letting files take
    # the target's own clock avoids that. --checksum keeps the transfer
    # incremental despite the unstable mtimes (decide by content, not size+time).
    args = ["rsync", "-rlpgoD", "--checksum", "--delete"]
    for ex in PUSH_EXCLUDES:
        args.append(f"--exclude={ex}")
    dst = f"{target.ssh_host}:{target.builddir}/" if target.ssh_host \
        else f"{target.builddir}/"
    args += [f"{staging}/", dst]
    return args


# ---------------------------------------------------------------------------
# workflow skip-list parsing
# ---------------------------------------------------------------------------

# The trailing '? tolerates a `bash -c '... make check'` wrapper (e.g. Cygwin).
_SKIP_RE = re.compile(r"RSYNC_EXPECT_SKIPPED=(\S+)\s+make\s+check'?\s*$", re.M)


def parse_workflow_skip(workflow: str) -> str | None:
    """Return the literal RSYNC_EXPECT_SKIPPED csv for the `make check` step, or
    None if the workflow leaves it unset."""
    path = WORKFLOWS / workflow
    try:
        text = path.read_text()
    except OSError:
        return None
    m = _SKIP_RE.search(text)
    return m.group(1) if m else None


# ---------------------------------------------------------------------------
# non-root test discovery
# ---------------------------------------------------------------------------

# A test opts into the fleet's extra non-root pass by setting a module-level
# `fleet_nonroot = True`. We read it with a text scan rather than importing the
# module (test files execute their body on import), so a new privilege-sensitive
# test joins the pass just by carrying the marker -- no fleet-config edit needed.
_NONROOT_RE = re.compile(r"^[ \t]*fleet_nonroot[ \t]*=[ \t]*True\b", re.M)


def discover_nonroot_tests(testsuite_dir: Path) -> list[str]:
    """Return the names (without the _test.py suffix) of the tests under
    testsuite_dir that declare `fleet_nonroot = True`."""
    names = []
    for p in sorted(testsuite_dir.glob("*_test.py")):
        try:
            if _NONROOT_RE.search(p.read_text(errors="replace")):
                names.append(p.name[: -len("_test.py")])
        except OSError:
            continue
    return names


# ---------------------------------------------------------------------------
# remote script builders
# ---------------------------------------------------------------------------


def build_script(t: Target) -> str:
    flags = " ".join(t.configure_flags)
    # configure only when not yet configured (keeps incremental builds fast);
    # --clean wipes the builddir beforehand so Makefile is absent -> reconfigure.
    pre = f'{t.env_prefix}\n' if t.env_prefix else ''
    return (
        f'cd {t.builddir} || exit 3\n'
        f'{pre}'
        f'if [ ! -f Makefile ]; then {t.configure_pre} ./configure {flags} || exit 4; fi\n'
        f'{t.make} -j{t.pipe_jobs} check-progs || exit 5\n'
    )


def test_script(t: Target, transport: str, skip_csv: str | None, jobs: int,
                protocol: int | None = None, only: list[str] | None = None) -> str:
    rb = f'--rsync-bin="$PWD/{t.rsync_bin}"'
    tcp = " --use-tcp" if transport == "tcp" else ""
    # protocol forces an older wire version (mirrors `make check30`/`check29`).
    proto = f" --protocol={protocol}" if protocol is not None else ""
    # PYTHONDONTWRITEBYTECODE: don't drop root-owned __pycache__/*.pyc into the
    # tree (a sudo run would, breaking the next non-root push --delete).
    env = "PYTHONDONTWRITEBYTECODE=1 "
    # Named tests (a max_retry re-run) make runtests full_run False, so the
    # expected-skip list does not apply -- only the named tests' pass/fail matter.
    names = ""
    if only:
        names = " " + " ".join(only)
    elif skip_csv:
        env += f"RSYNC_EXPECT_SKIPPED={skip_csv} "
    runtests = f'{t.python} runtests.py {rb}{tcp}{proto} -j {jobs}{names}'
    # env_prefix (e.g. a brew PATH) must reach the test too: some tests build a
    # helper binary on the fly (a test may invoke `make`, which needs gawk etc.),
    # so the build tools must be on PATH at test time.
    pre = f'{t.env_prefix}; ' if t.env_prefix else ''
    if t.privilege == "sudo":
        # -n: never prompt (capture_output has no TTY -- a prompt would hang
        # the whole timeout). Targets need passwordless sudo or a fresh
        # `sudo -v`. env keeps the vars (and PATH) across the sudo boundary.
        path_pass = 'PATH="$PATH" ' if t.env_prefix else ''
        cmd = f"{pre}sudo -n env {path_pass}{env}{runtests}"
    else:
        cmd = pre + env + runtests
    return f'cd {t.builddir} || exit 3\n{cmd}\n'


def nonroot_test_script(t: Target, names: list[str]) -> str:
    """Run the given tests as the (non-root) ssh user -- the fleet analogue of a
    workflow's non-root check step. Explicit test names make runtests.py
    full_run False, so no RSYNC_EXPECT_SKIPPED is involved; only FAILs matter.
    The prior sudo pipe/tcp runs left testtmp root-owned, so clear it (via sudo)
    before the non-root run recreates it."""
    pre = f'{t.env_prefix}; ' if t.env_prefix else ''
    runtests = (f'PYTHONDONTWRITEBYTECODE=1 {t.python} runtests.py '
                f'--rsync-bin="$PWD/{t.rsync_bin}" {" ".join(names)}')
    return (f'cd {t.builddir} || exit 3\n'
            f'sudo -n rm -rf testtmp\n'
            f'{pre}{runtests}\n')


# ---------------------------------------------------------------------------
# runtests.py output parsing
# ---------------------------------------------------------------------------

RE_RESULT = re.compile(r"^(PASS|FAIL|ERROR|XFAIL|SKIP)\s+(\S+)", re.M)
RE_COUNT = re.compile(r"^\s+(\d+)\s+(passed|failed|xfailed|skipped)\b", re.M)
RE_SKIP_HDR = re.compile(r"^----- skipped results:", re.M)
RE_SKIP_EXP = re.compile(r"^\s+expected:\s*(.*)$", re.M)
RE_SKIP_GOT = re.compile(r"^\s+got:\s*(.*)$", re.M)


def _csv_set(s: str) -> set[str]:
    return {x for x in s.strip().split(",") if x}


@dataclasses.dataclass
class TransportResult:
    transport: str
    exit_code: int
    timed_out: bool
    counts: dict[str, int]
    failed: list[str]
    skip_checked: bool
    skip_expected: set[str]
    skip_got: set[str]
    raw: str
    # Tests that failed the initial run but passed on a max_retry re-run, so they
    # were dropped from `failed`.  Surfaced in the report (a recovered flake is
    # noted, never silently hidden).
    recovered: list[str] = dataclasses.field(default_factory=list)

    @property
    def skip_mismatch(self) -> bool:
        return self.skip_checked and self.skip_expected != self.skip_got

    @property
    def ok(self) -> bool:
        return (not self.timed_out and self.exit_code == 0
                and not self.failed and not self.skip_mismatch)


def parse_transport(transport: str, r: CmdResult, skip_checked: bool) -> TransportResult:
    counts = {"passed": 0, "failed": 0, "xfailed": 0, "skipped": 0}
    for m in RE_COUNT.finditer(r.out):
        counts[m.group(2)] = int(m.group(1))
    failed = [m.group(2) for m in RE_RESULT.finditer(r.out)
              if m.group(1) in ("FAIL", "ERROR")]
    exp = got = set()
    if skip_checked and RE_SKIP_HDR.search(r.out):
        em = RE_SKIP_EXP.search(r.out)
        gm = RE_SKIP_GOT.search(r.out)
        exp = _csv_set(em.group(1)) if em else set()
        got = _csv_set(gm.group(1)) if gm else set()
    return TransportResult(transport, r.rc, r.timed_out, counts, failed,
                           skip_checked, exp, got, r.out)


def retry_failed(t: Target, label: str, tr: TransportResult, rerun) -> None:
    """Honour the target's max_retry budget: re-run each failed test on its own
    (serially) up to max_retry more times; drop any that pass and record them in
    tr.recovered.  `rerun(names)` runs the given tests and returns a CmdResult.
    A no-op when max_retry is 0 or there were no failures."""
    if not t.max_retry or not tr.failed:
        return
    remaining = list(tr.failed)
    for attempt in range(1, t.max_retry + 1):
        r = rerun(remaining)
        still = [m.group(2) for m in RE_RESULT.finditer(r.out)
                 if m.group(1) in ("FAIL", "ERROR")]
        recovered = [n for n in remaining if n not in still]
        if recovered:
            tr.recovered.extend(recovered)
            log(f"[{t.name}] {label} retry {attempt}/{t.max_retry}: "
                f"recovered {','.join(recovered)}"
                + (f"; still failing {','.join(still)}" if still else ""))
        remaining = [n for n in remaining if n in still]
        if not remaining:
            break
    tr.failed = remaining
    # The initial run's non-zero exit was the now-recovered failures; once they
    # all pass on retry the cell is OK, so clear the stale exit code (only the
    # failed tests can make runtests exit non-zero on a no-skip-list re-run).
    if not remaining and tr.recovered and tr.exit_code != 0:
        tr.exit_code = 0


@dataclasses.dataclass
class TargetResult:
    target: str
    reachable: bool = True
    pushed: bool = True
    build_ok: bool = True
    error: str = ""
    build_log: str = ""
    transports: dict[str, TransportResult] = dataclasses.field(default_factory=dict)
    # Wall-clock seconds per phase (push/build/pipe/tcp/nonroot) plus "total";
    # populated for --timing. Phases run sequentially, so they sum to the total.
    timings: dict[str, float] = dataclasses.field(default_factory=dict)


# ---------------------------------------------------------------------------
# per-target worker
# ---------------------------------------------------------------------------

_print_lock = threading.Lock()


def log(msg: str) -> None:
    with _print_lock:
        print(msg, flush=True)


def run_target(t: Target, args, staging: str) -> TargetResult:
    res = TargetResult(t.name)
    log(f"[{t.name}] start")
    started = time.monotonic()

    if t.ssh_host:
        ping = run_on(t, "echo ok", timeout=25)
        if ping.rc != 0:
            res.reachable = False
            res.error = f"ssh unreachable (rc={ping.rc}): {ping.out.strip()[:200]}"
            log(f"[{t.name}] UNREACHABLE")
            return res

    # Always push: the run dir is freshly named per run, so there is no prior
    # tree to reuse -- every run is a full configure + build.
    t0 = time.monotonic()
    push = subprocess.run(push_argv(t, staging),
                          capture_output=True, text=True, timeout=600)
    res.timings["push"] = time.monotonic() - t0
    if push.returncode != 0:
        res.pushed = False
        res.error = f"push failed (rc={push.returncode}): {push.stderr.strip()[:300]}"
        log(f"[{t.name}] PUSH-FAIL")
        return res

    t0 = time.monotonic()
    b = run_on(t, build_script(t), timeout=1200)
    res.timings["build"] = time.monotonic() - t0
    res.build_ok = b.rc == 0
    res.build_log = b.out
    if not res.build_ok:
        log(f"[{t.name}] BUILD-FAIL")
        return res

    for transport in args.transports:
        skip_csv = parse_workflow_skip(t.workflow) if transport == "pipe" else None
        jobs = (args.jobs if args.jobs else
                (t.tcp_jobs if transport == "tcp" else t.pipe_jobs))
        cmd = test_script(t, transport, skip_csv, jobs)
        t0 = time.monotonic()
        r = run_on(t, cmd, timeout=2400)
        res.timings[transport] = time.monotonic() - t0
        tr = parse_transport(transport, r, skip_csv is not None)
        retry_failed(t, transport, tr, lambda names, tp=transport: run_on(
            t, test_script(t, tp, None, 1, only=names), timeout=1200))
        res.transports[transport] = tr
        log(f"[{t.name}] {transport} done "
            f"({'ok' if tr.ok else 'ISSUE'})")

    # Extra older-protocol passes (mirroring the workflow's check30/check29
    # steps): same stdio-pipe transport and skip list as `make check`, but with
    # runtests --protocol=N forcing an older wire version. Only targets that list
    # `protocols` opt in; skipped under --transport tcp (these are pipe runs).
    if t.protocols and "pipe" in args.transports:
        skip_csv = parse_workflow_skip(t.workflow)
        jobs = args.jobs if args.jobs else t.pipe_jobs
        for proto in t.protocols:
            label = f"proto{proto}"
            cmd = test_script(t, "pipe", skip_csv, jobs, protocol=proto)
            t0 = time.monotonic()
            r = run_on(t, cmd, timeout=2400)
            res.timings[label] = time.monotonic() - t0
            tr = parse_transport(label, r, skip_csv is not None)
            retry_failed(t, label, tr, lambda names, pr=proto: run_on(
                t, test_script(t, "pipe", None, 1, protocol=pr, only=names),
                timeout=1200))
            res.transports[label] = tr
            log(f"[{t.name}] {label} done "
                f"({'ok' if tr.ok else 'ISSUE'})")

    # Extra non-root pass (after the sudo runs) for targets that opt in, running
    # the tests that declare `fleet_nonroot = True` (discovered in main()).
    if t.nonroot and args.nonroot_tests:
        t0 = time.monotonic()
        r = run_on(t, nonroot_test_script(t, args.nonroot_tests), timeout=2400)
        res.timings["nonroot"] = time.monotonic() - t0
        tr = parse_transport("nonroot", r, skip_checked=False)
        retry_failed(t, "nonroot", tr, lambda names: run_on(
            t, nonroot_test_script(t, names), timeout=1200))
        res.transports["nonroot"] = tr
        log(f"[{t.name}] nonroot done "
            f"({'ok' if tr.ok else 'ISSUE'})")
    res.timings["total"] = time.monotonic() - started
    return res


# ---------------------------------------------------------------------------
# reporting
# ---------------------------------------------------------------------------


def cell_status(res: TargetResult, transport: str) -> str:
    if not res.reachable:
        return "UNREACHABLE"
    if not res.pushed:
        return "PUSH-FAIL"
    if not res.build_ok:
        return "BUILD-FAIL"
    tr = res.transports.get(transport)
    if tr is None:
        return "-"
    if tr.timed_out:
        return "TIMEOUT"
    if tr.failed:
        return f"FAIL({len(tr.failed)})"
    if tr.skip_mismatch:
        return "SKIP-MISMATCH"
    if tr.exit_code != 0:
        return f"EXIT({tr.exit_code})"
    return "OK"


def print_report(results: list[TargetResult], args, fleet: list[Target]) -> bool:
    by_name = {t.name: t for t in fleet}
    order = {t.name: i for i, t in enumerate(fleet)}
    results.sort(key=lambda r: order.get(r.target, 99))
    # protoNN columns appear only when some target ran that older-protocol pass;
    # the 'nonroot' column only when some target ran a non-root pass. Targets
    # without a given pass show "-" there (a neutral N/A, not a failure).
    transports = list(args.transports)
    protos = {k for r in results for k in r.transports if k.startswith("proto")}
    # highest protocol first (proto30 before proto29), matching check30/check29.
    transports += sorted(protos, key=lambda c: int(c[len("proto"):]), reverse=True)
    if any("nonroot" in r.transports for r in results):
        transports.append("nonroot")
    ts = time.strftime("%Y-%m-%d %H:%M")
    print("\n" + "=" * 64)
    print(f"rsync fleet CI — branch {current_branch()} — {ts}")
    print(f"source: HEAD   run: {args.run_id}   "
          f"transports: {','.join(args.transports)}")
    print("(A target's pipe skip-set is only enforced when its workflow sets "
          "RSYNC_EXPECT_SKIPPED; otherwise only FAILs matter. The 'nonroot' "
          "column runs the privilege-sensitive tests as the unprivileged user; "
          "'-' = N/A.)")
    print("=" * 64)

    width = max(len(t) for t in order) + 2
    header = "TARGET".ljust(width) + "".join(tr.upper().ljust(16) for tr in transports)
    print(header)
    all_ok = True
    for res in results:
        row = res.target.ljust(width)
        for transport in transports:
            st = cell_status(res, transport)
            if st not in ("OK", "-"):    # "-" = N/A (e.g. no nonroot pass)
                all_ok = False
            row += st.ljust(16)
        # data-driven row notes: local target, or a target with a distinct tcp -j
        t = by_name.get(res.target)
        notes = []
        if t is not None:
            if t.ssh_host is None:
                notes.append("(local)")
            if "tcp" in transports and t.tcp_jobs != t.pipe_jobs:
                notes.append(f"(tcp -j{t.tcp_jobs})")
        print(row + " ".join(notes))

    # detail section: only the unexpected cells
    details: list[str] = []
    for res in results:
        if not res.reachable:
            details.append(f"{res.target} — UNREACHABLE: {res.error}")
            continue
        if not res.pushed:
            details.append(f"{res.target} — PUSH-FAIL: {res.error}")
            continue
        if not res.build_ok:
            tail = "\n    ".join(res.build_log.strip().splitlines()[-20:])
            details.append(f"{res.target} — BUILD-FAIL:\n    {tail}")
            continue
        for transport in transports:
            tr = res.transports.get(transport)
            if tr is None or tr.ok:
                continue
            if tr.timed_out:
                details.append(f"{res.target} / {transport} — TIMEOUT")
            if tr.failed:
                details.append(f"{res.target} / {transport} — {len(tr.failed)} failed:\n    "
                               + " ".join(tr.failed))
            if tr.skip_mismatch:
                extra = tr.skip_got - tr.skip_expected
                missing = tr.skip_expected - tr.skip_got
                diff = []
                if extra:
                    diff.append(f"unexpected skips: {','.join(sorted(extra))}")
                if missing:
                    diff.append(f"expected-but-ran: {','.join(sorted(missing))}")
                details.append(f"{res.target} / {transport} — skip mismatch ("
                               + "; ".join(diff) + ")\n"
                               f"    expected: {','.join(sorted(tr.skip_expected))}\n"
                               f"    got:      {','.join(sorted(tr.skip_got))}")
            elif not tr.failed and not tr.timed_out and tr.exit_code != 0:
                details.append(f"{res.target} / {transport} — runtests exit {tr.exit_code}")

    # Exclude N/A ("-") cells (e.g. the nonroot column for targets that don't
    # run a non-root pass) from the OK/not-OK tally.
    statuses = [cell_status(res, transport)
                for res in results for transport in transports]
    cells = sum(1 for s in statuses if s != "-")
    ok_cells = sum(1 for s in statuses if s == "OK")
    print("=" * 64)
    if details:
        print("==== UNEXPECTED RESULTS ====")
        for d in details:
            print(d)
        print("=" * 64)
    # Recovered flakes: tests that failed but passed within the target's
    # max_retry budget.  The cell counts as OK, but list them so a flaky test is
    # never silently swallowed.
    recovered = [f"{res.target} / {transport}: {','.join(tr.recovered)}"
                 for res in results for transport in transports
                 if (tr := res.transports.get(transport)) and tr.recovered]
    if recovered:
        print("==== RECOVERED (flaky -- failed, then passed on retry) ====")
        for r in recovered:
            print(f"    {r}")
        print("=" * 64)
    print(f"{len(results)} targets x {len(transports)} transports = {cells} cells: "
          f"{ok_cells} OK, {cells - ok_cells} not OK")
    return all_ok


# Phase columns for --timing, in execution order (push -> build -> tests).
_TIMING_PHASES = ("push", "build", "pipe", "tcp", "nonroot")


def _fmt_dur(s: float) -> str:
    if s < 60:
        return f"{s:.0f}s"
    m, sec = divmod(int(round(s)), 60)
    return f"{m}m{sec:02d}s"


def print_timing(results: list[TargetResult]) -> None:
    """Per-target wall-clock breakdown, slowest first. Targets run in parallel,
    so the whole run is gated by the slowest one -- that's the hold-up; the
    phase columns show whether it's push, build or the test passes."""
    timed = [r for r in results if r.timings]
    if not timed:
        return
    # Insert any protoNN phases (highest first) just before nonroot, in run order.
    protos = sorted({k for r in timed for k in r.timings if k.startswith("proto")},
                    key=lambda c: int(c[len("proto"):]), reverse=True)
    order = [p for p in _TIMING_PHASES if p != "nonroot"] + protos + ["nonroot"]
    phases = [p for p in order if any(p in r.timings for r in timed)]

    def total(r: TargetResult) -> float:
        # Failed-early targets have no "total"; sum the phases they did reach.
        return r.timings.get("total") or sum(r.timings.get(p, 0.0) for p in phases)

    timed.sort(key=total, reverse=True)
    width = max([len("TARGET")] + [len(r.target) for r in timed]) + 2
    print("\n==== TIMING (slowest target first) ====")
    print("TARGET".ljust(width) + "TOTAL".ljust(9)
          + "".join(p.upper().ljust(9) for p in phases))
    for r in timed:
        row = r.target.ljust(width) + _fmt_dur(total(r)).ljust(9)
        for p in phases:
            v = r.timings.get(p)
            row += (_fmt_dur(v) if v is not None else "-").ljust(9)
        print(row)
    slow = timed[0]
    print(f"hold-up: {slow.target} at {_fmt_dur(total(slow))} gates the run "
          "(targets run in parallel)")


def current_branch() -> str:
    try:
        return subprocess.run(["git", "-C", str(REPO), "rev-parse",
                               "--abbrev-ref", "HEAD"],
                              capture_output=True, text=True).stdout.strip() or "?"
    except Exception:
        return "?"


# ---------------------------------------------------------------------------
# run-dir cleanup
# ---------------------------------------------------------------------------

# Targets whose per-run dir (t.builddir, already suffixed with the run_id) this
# process must remove on exit. Populated in main() once the run_id is applied.
_cleanup_targets: list[Target] = []
_cleanup_lock = threading.Lock()
_cleanup_done = False


def _unsafe_builddir(path: str) -> bool:
    """True if `path` is too broad to feed to `rm -rf` -- empty, root, $HOME, or
    an absolute path sitting directly under / (e.g. /tmp). A real run dir is
    always nested deeper, so this rejects an obvious builddir misconfiguration
    before any destructive command is built."""
    p = (path or "").rstrip("/")
    if p in ("", "/", "~") or os.path.expanduser(p) == os.path.expanduser("~"):
        return True
    return os.path.isabs(p) and os.path.dirname(p) == "/"


def cleanup_run() -> None:
    """Best-effort `rm -rf` of this run's dir on every chosen target. Idempotent
    (atexit + a signal handler may both call it). Each target removes only its
    own <base>-<run_id> dir, so a concurrent run's dir is never touched."""
    global _cleanup_done
    with _cleanup_lock:
        if _cleanup_done or not _cleanup_targets:
            return
        _cleanup_done = True
        targets = list(_cleanup_targets)
    for t in targets:
        if _unsafe_builddir(t.builddir):
            continue
        run_on(t, f'rm -rf -- {t.builddir}', timeout=60)


def _on_signal(signum, frame):
    cleanup_run()
    # Skip atexit/thread-join: worker threads' ssh calls can't be cancelled and
    # would otherwise block exit until they return. The remote build/test simply
    # errors out now that its dir is gone.
    os._exit(130 if signum == signal.SIGINT else 143)


def cleanup_remnants(targets: list[Target]) -> int:
    """--cleanup mode: remove every <base>-* run dir on each target, reporting
    what each removed. Returns a process exit code. Only suffixed run dirs are
    swept -- a bare <base> is left alone."""
    rc = 0
    for t in targets:
        base = t.builddir
        if _unsafe_builddir(base):
            log(f"[{t.name}] skipped (unsafe builddir {base!r})")
            continue
        # Echo each match before removing it so the harness can report what
        # went; an unmatched glob stays literal and is skipped by the -e test.
        script = (f'set -e\n'
                  f'for d in {base}-*; do\n'
                  f'  [ -e "$d" ] || continue\n'
                  f'  echo "$d"\n'
                  f'  rm -rf -- "$d"\n'
                  f'done\n')
        r = run_on(t, script, timeout=120)
        removed = [ln for ln in r.out.splitlines() if ln.strip()]
        if r.rc != 0:
            rc = 1
            log(f"[{t.name}] cleanup error (rc={r.rc}): {r.out.strip()[:200]}")
        elif removed:
            log(f"[{t.name}] removed: {' '.join(removed)}")
        else:
            log(f"[{t.name}] nothing to remove")
    return rc


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser(description="Fleet CI harness for rsync.")
    ap.add_argument("--targets", help="comma-separated subset (default: all)")
    ap.add_argument("--transport", choices=["pipe", "tcp", "both"], default="both")
    ap.add_argument("--keep", action="store_true",
                    help="keep each run's build dir (default: remove it at exit)")
    ap.add_argument("--cleanup", action="store_true",
                    help="remove stray <builddir>-* run dirs on the targets, then exit")
    ap.add_argument("--jobs", type=int, help="override -j for both transports")
    ap.add_argument("--timing", action="store_true",
                    help="report per-target wall-clock (push/build/test) to find "
                    "the slowest target")
    ap.add_argument("--repo", help="rsync source tree to build (default: cwd)")
    ap.add_argument("--fleet", help="fleet config JSON (default: ~/.fleettest.json, "
                    "else fleettest.json next to this script)")
    ap.add_argument("--list", action="store_true", help="list targets and exit")
    args = ap.parse_args()

    global REPO, WORKFLOWS
    REPO = Path(args.repo).resolve() if args.repo else Path.cwd()
    WORKFLOWS = REPO / ".github" / "workflows"
    if not args.cleanup and not (REPO / "runtests.py").is_file():
        print(f"{REPO} is not an rsync source tree (no runtests.py); "
              f"run from inside a checkout or pass --repo", file=sys.stderr)
        return 2

    if args.fleet:
        config_path = Path(args.fleet).resolve()
        if not config_path.exists():
            print(f"no fleet config at {config_path}", file=sys.stderr)
            return 2
    else:
        config_path = next((p for p in DEFAULT_CONFIGS if p.exists()), None)
        if config_path is None:
            tried = " or ".join(str(p) for p in DEFAULT_CONFIGS)
            print(f"no fleet config found (looked for {tried})\n"
                  f"copy {EXAMPLE_CONFIG} to {SCRIPT_CONFIG} or {HOME_CONFIG} "
                  f"(or pass --fleet PATH)", file=sys.stderr)
            return 2
    fleet = load_fleet(config_path)

    if args.list:
        for t in fleet:
            host = t.ssh_host or "(local)"
            skip = parse_workflow_skip(t.workflow)
            proto = (",".join(f"proto{p}" for p in t.protocols)
                     if t.protocols else "none")
            print(f"{t.name:12} {host:18} {t.make:6} "
                  f"pipe-skip={'set' if skip else 'unset'} protocols={proto}")
        return 0

    chosen = fleet
    if args.targets:
        want = [s.strip() for s in args.targets.split(",") if s.strip()]
        by_name = {t.name: t for t in fleet}
        bad = [w for w in want if w not in by_name]
        if bad:
            print(f"unknown target(s): {', '.join(bad)}", file=sys.stderr)
            print(f"known: {', '.join(by_name)}", file=sys.stderr)
            return 2
        chosen = [by_name[w] for w in want]

    if args.cleanup:
        # Sweep every <builddir>-* run dir on the selected targets. NB: this
        # also removes dirs belonging to runs that are still in progress, so
        # only run it when no other fleettest runs are active (or scope with
        # --targets).
        return cleanup_remnants(chosen)

    args.transports = ["pipe", "tcp"] if args.transport == "both" else [args.transport]

    # Give this run its own build dir on every target so concurrent runs don't
    # collide: <builddir>-<run_id>. The base name is the prefix --cleanup globs.
    args.run_id = secrets.token_hex(3)
    for t in chosen:
        t.builddir = f"{t.builddir}-{args.run_id}"
    log(f"run {args.run_id}: build dir <target>:{chosen[0].builddir} "
        f"(removed at exit; --keep to retain)")

    # Remove each run dir when we exit -- success or failure, and best-effort on
    # Ctrl-C/kill (a signal mid-push may still leave a remnant). SIGKILL can't be
    # caught; `fleettest.py --cleanup` sweeps any such remnant.
    if not args.keep:
        _cleanup_targets.extend(chosen)
        atexit.register(cleanup_run)
        signal.signal(signal.SIGINT, _on_signal)
        signal.signal(signal.SIGTERM, _on_signal)

    # Stage committed HEAD (source-only). Each target regenerates its own
    # build files with its own toolchain -- exactly like the CI jobs, which
    # install autotools / python-markdown / dev-libs in their prepare step.
    # (Pushing locally-generated files instead fights rsync's Makefile
    # maintainer rules: a target with a different autoconf version sees
    # "configure.sh has CHANGED" and errors.) So each target must be
    # provisioned like its workflow -- see the module docstring.
    staging = tempfile.mkdtemp(prefix="rsync-fleettest-stage.")
    try:
        ar = subprocess.run(f"git -C {REPO} archive HEAD | tar -x -C {staging}",
                            shell=True, capture_output=True, text=True)
        if ar.returncode != 0:
            print(f"git archive failed: {ar.stderr}", file=sys.stderr)
            return 2

        # Tests that opt into the non-root pass (same for every target).
        args.nonroot_tests = discover_nonroot_tests(Path(staging) / "testsuite")

        results: list[TargetResult] = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(chosen)) as ex:
            futs = {ex.submit(run_target, t, args, staging): t for t in chosen}
            for fut in concurrent.futures.as_completed(futs):
                t = futs[fut]
                try:
                    results.append(fut.result())
                except Exception as e:  # never let one target kill the run
                    r = TargetResult(t.name)
                    r.reachable = False
                    r.error = f"harness exception: {e!r}"
                    results.append(r)
    finally:
        subprocess.run(["rm", "-rf", staging])

    all_ok = print_report(results, args, fleet)
    if args.timing:
        print_timing(results)
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())

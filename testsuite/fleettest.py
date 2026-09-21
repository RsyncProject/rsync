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

The tcp pass runs only the tests that can reach the daemon transport, because it
follows a full pipe pass over the very same build: --use-tcp is observable only
through rsyncfns' start_test_daemon(), so a test that never calls it produces
the same result twice. Pass --full-tcp to run the whole suite there anyway.

A target may also list older "protocols" (e.g. [30, 29]) in the fleet config:
each runs as an extra stdio-pipe pass with runtests --protocol=N (the fleet
analogue of a workflow's check30/check29 steps), using that step's own parsed
skip list, and shows up as a protoNN column in the report.

The fleet -- which machines, how to reach and build each -- is read from a JSON
config: ~/.fleettest.json if present, else fleettest.json next to this script,
or --fleet PATH. Copy the bundled fleettest.json.example to either location (or
symlink it) and edit for your own hosts; see testsuite/README.md and the
comments in fleettest.json.example.

Source = `git archive HEAD` of the rsync tree (the current directory, or --repo
PATH) -- source-only, no .o/binaries are ever pushed.

Every run uses its own randomly-named build directory on each target
(<builddir>-<run_id>-<target>), so two or three fleettest runs can share the same
fleet without interfering: each pushes, builds and tests in isolation. The run dir
is removed when the run ends -- on success or failure, and best-effort on
Ctrl-C/kill (pass --keep to retain it for inspection).

--keep-on-fail is the cheaper half of --keep: only the targets that came back
with something unexpected keep their run dir, and their full build/test output
is also written locally under fleettest-logs/<run_id>/<target>/. A fleet run
costs a full build on every machine, so re-running just to see why one test
failed is expensive -- and a race test may not fail the same way twice.

A run that is hard-killed
(SIGKILL), or signalled mid-push, or whose ssh dies during cleanup can leave a
stray <builddir>-<id>-<target> behind -- plus an orphaned path-flipper or test
rsyncd on platforms without a parent-death backstop; sweep all of those
(root-owned files included, via sudo -n) with `fleettest.py --cleanup` (optionally
scoped with --targets). Run --cleanup between runs, not during one: its process
kills are host-global and would also catch a concurrent run's flipper/daemon.
Because each run starts from a fresh dir, every build is a full configure + build.

Targets run concurrently, EXCEPT that targets naming the same machine run one
after another. More than one target may point at a single host -- a variant that
tests the same build on a different filesystem does -- and those cannot overlap:
they would fight over the fixed ports the daemon tests claim, and over any other
host-global state.

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
    python3 testsuite/fleettest.py --keep-on-fail  # ...but only where it broke
    python3 testsuite/fleettest.py --timing        # per-target AND per-test times
    python3 testsuite/fleettest.py --cleanup       # sweep stray run dirs, exit
    python3 testsuite/fleettest.py --fleet my-fleet.json --list

Exit 0 iff every selected (target x transport) cell is OK.
"""

from __future__ import annotations

import argparse
import atexit
import concurrent.futures
import dataclasses
import fnmatch
import json
import os
import shlex
import re
import secrets
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

# Set from --skip / --xfail in main(). SKIP_CSV is passed to runtests as
# RSYNC_EXCLUDE (tests dropped before running); XFAIL_GLOBS are tolerated
# failures (a matching FAIL does not make a cell "not OK"). Both are
# comma-separated test-name globs (fnmatch), applied across every target.
SKIP_CSV = ""
# Names from a backport tree's testsuite/skiplist/backport.txt (see main()).
BACKPORT_EXCLUDE: list[str] = []
XFAIL_GLOBS: list[str] = []

# Set from --timing in main(). Also asks each target's runtests.py for its own
# per-test wall-clock table, so a slow cell can be attributed to actual tests.
TIMING = False

# Set from --full-tcp in main(): run the WHOLE suite in the tcp pass rather than
# only the tests that can observe the transport. The narrow pass is the default
# because the tcp run follows a full pipe run over the very same build.
FULL_TCP = False

# The transports this run will execute (from --transport), needed in test_script
# to tell "tcp after a pipe pass" from "tcp is the only pass".
TRANSPORTS: list[str] = []

# Set from --repo in main() (default: cwd). The harness builds whatever rsync
# source tree these point at, so it must be run from inside an rsync checkout
# or given --repo PATH.
REPO = Path.cwd()
# Source tree providing the test suite (runtests.py + testsuite/). Defaults to
# REPO; --testsuite-repo decouples it so one tree is built and another's suite is
# run against the result.
TESTSUITE_REPO = REPO
WORKFLOWS = TESTSUITE_REPO / ".github" / "workflows"

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
    scratchbase: str = ""         # run the tests' scratch trees here (e.g. another filesystem)
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
    # Test names this specific box skips beyond what its workflow lists -- e.g. an
    # old-kernel fleet box (no openat2/RESOLVE_BENEATH) skips the RB-conditional
    # symlink-race tests that the workflow's newer CI runner actually runs.  Merged
    # into RSYNC_EXPECT_SKIPPED for each pipe/protocol pass the workflow itself
    # pins -- a pass with no matching workflow step stays unpinned (see
    # workflow_skip_for).
    expect_skip_extra: list[str] = dataclasses.field(default_factory=list)
    # ...and the mirror: entries the workflow expects to skip which this target
    # actually RUNS (a relocated scratch can satisfy a condition the
    # workflow's host cannot, e.g. a cross-device copy).
    expect_skip_omit: list[str] = dataclasses.field(default_factory=list)
    # Test-name globs this box never runs (passed to runtests as RSYNC_EXCLUDE),
    # for tests unreliable on this platform for a non-rsync reason -- e.g. the
    # daemon+flipper symlink-race tests on openbsd, which the platform's kernel
    # connect()-under-rename-load bug hangs (see dev-notes). Merged with --skip.
    exclude: list[str] = dataclasses.field(default_factory=list)
    # Test-name globs whose failure is tolerated on THIS box (merged with the
    # global --xfail), for a known platform/version-specific failure that should
    # not fail the cell -- e.g. crtimes on macOS for an older binary. The test
    # still runs; if it passes, the entry is simply a no-op.
    xfail: list[str] = dataclasses.field(default_factory=list)


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

def parse_workflow_skip(workflow: str, make_target: str = "check") -> str | None:
    """Return the literal RSYNC_EXPECT_SKIPPED spec for the given `make <target>`
    step (check / check30 / check29), or None if that step leaves it unset.  The
    protocol passes have their own check30/check29 lines (e.g. an xattr/ACL test
    that runs at proto 30 but skips at 29), so they must be parsed separately from
    the plain pipe `make check`.  The trailing '? tolerates a `bash -c '... make
    check'` wrapper (e.g. Cygwin).

    The spec is passed through to the remote runtests.py verbatim; @FILE entries
    (testsuite/skiplist/*.txt) are expanded there, against the staged tree, so
    the list always matches the tests that shipped with it."""
    path = WORKFLOWS / workflow
    try:
        text = path.read_text()
    except OSError:
        return None
    rx = re.compile(r"RSYNC_EXPECT_SKIPPED=(\S+)\s+make\s+"
                    + re.escape(make_target) + r"'?\s*$", re.M)
    m = rx.search(text)
    return m.group(1) if m else None


def _expand_spec_names(spec: str) -> set[str]:
    """The test names a workflow's RSYNC_EXPECT_SKIPPED spec resolves to, by
    reading its @FILE references out of the suite tree.  Only used to decide
    which backport exclusions are actually IN the expected-skip set: runtests
    rejects a '-name' that removes a name nothing added, so a removal may only
    be emitted for a name the spec really contains."""
    names: set[str] = set()
    for tok in spec.split(","):
        tok = tok.strip()
        if not tok or tok.startswith("-"):
            continue
        if tok.startswith("@"):
            f = TESTSUITE_REPO / tok[1:]
            try:
                for ln in f.read_text().splitlines():
                    ln = ln.split("#", 1)[0].strip()
                    if ln:
                        names.add(ln)
            except OSError:
                pass
        else:
            names.add(tok)
    return names


def workflow_skip_for(t: "Target", make_target: str = "check") -> str | None:
    """The target's expected-skip csv for a `make <target>` pass: its workflow's
    RSYNC_EXPECT_SKIPPED, plus any per-target expect_skip_extra (old-box-only
    skips the workflow omits) and minus any expect_skip_omit (tests the workflow
    expects to skip which this target can actually run).

    The workflow spec is now mostly @FILE references, which are expanded on the
    target rather than here, so an omission cannot be done by set subtraction:
    the name to drop lives inside a file we deliberately do not read.  It is
    emitted as a '-name' token instead, which runtests.py applies after every
    addition.

    None (no oracle) when the workflow has no such step -- e.g. a protocols=[29]
    target whose workflow has no check29 line.  The extras alone would be a
    near-empty expected set and so a guaranteed mismatch; a lane the workflow
    does not pin is simply not pinned here either."""
    base = parse_workflow_skip(t.workflow, make_target)
    # A backport-excluded test never runs, so it cannot skip either: drop it
    # from the expected-skip set as well, or the oracle waits for a skip that
    # can no longer happen.  Only for names the spec actually contains --
    # runtests rejects a '-name' that removes a name nothing added.
    in_spec = _expand_spec_names(base) if (base and BACKPORT_EXCLUDE) else set()
    omit = set(t.expect_skip_omit) | (set(BACKPORT_EXCLUDE) & in_spec)
    if base is None or not (t.expect_skip_extra or omit):
        return base
    items = sorted(set(t.expect_skip_extra) | ({base} if base else set()))
    # A backport-excluded test cannot skip, because it never runs -- so it must
    # also come OUT of the expected-skip set, or the oracle waits for a skip
    # that can no longer happen.  The name usually lives inside an @FILE, so it
    # is dropped with a '-name' token that runtests applies after expansion.
    items += [f"-{n}" for n in sorted(omit)]
    return ",".join(items)


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


def _exclude_csv(t: "Target") -> str:
    """RSYNC_EXCLUDE csv for a target: the global --skip globs plus this target's
    per-box `exclude` list (tests it never runs, e.g. the openbsd kernel-bug
    daemon+flipper races)."""
    parts = [p for p in ([SKIP_CSV] + list(t.exclude)) if p]
    return ",".join(parts)


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
    if t.scratchbase:
        # Must travel in `env`, not env_prefix: the sudo branch below runs
        # `sudo -n env ...`, which drops whatever the outer shell exported.
        env += f"scratchbase={shlex.quote(t.scratchbase)} "
    excl = _exclude_csv(t)
    if excl:
        env += f"RSYNC_EXCLUDE={excl} "
    # Named tests (a max_retry re-run) make runtests full_run False, so the
    # expected-skip list does not apply -- only the named tests' pass/fail matter.
    names = ""
    if only:
        names = " " + " ".join(only)
    elif skip_csv:
        env += f"RSYNC_EXPECT_SKIPPED={skip_csv} "
    # --timing makes the remote runtests print its own per-test wall-clock table,
    # which lands in the captured output: that is where a "this target is slow"
    # cell turns into "these tests are slow on this target".
    timing = " --timing" if TIMING and not only else ""
    # --use-tcp is observable only through start_test_daemon(), so when the tcp
    # pass follows a full pipe pass over the same build, the tests that never
    # reach it would just produce the same result twice: narrow it to the ones
    # that can tell the difference. That reasoning depends ENTIRELY on the pipe
    # pass having run -- under --transport tcp it is the only pass there is, and
    # narrowing it would silently drop the other 186 tests from the run
    # altogether. --full-tcp forces the full sweep either way.
    narrow_tcp = (transport == "tcp" and not FULL_TCP and not only
                  and "pipe" in TRANSPORTS)
    only_daemon = " --daemon-tests-only" if narrow_tcp else ""
    runtests = (f'{t.python} runtests.py {rb}{tcp}{proto} '
                f'-j {jobs}{timing}{only_daemon}{names}')
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
    _e = _exclude_csv(t)
    excl = f'RSYNC_EXCLUDE={_e} ' if _e else ''
    sb = f'scratchbase={shlex.quote(t.scratchbase)} ' if t.scratchbase else ''
    runtests = (f'PYTHONDONTWRITEBYTECODE=1 {sb}{excl}{t.python} runtests.py '
                f'--rsync-bin="$PWD/{t.rsync_bin}" {" ".join(names)}')
    # A relocated scratch lives outside builddir, so clearing ./testtmp alone
    # leaves the prior sudo run's root-owned tree in place and the non-root
    # pass cannot create anything under it.
    extra_rm = (f'sudo -n rm -rf {shlex.quote(t.scratchbase + "/testtmp")}\n'
                if t.scratchbase else '')
    return (f'cd {t.builddir} || exit 3\n'
            f'sudo -n rm -rf testtmp\n'
            f'{extra_rm}'
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
    # Tests whose failure was tolerated via --xfail: dropped from `failed` (so the
    # cell can still be OK) but surfaced in the report, never silently hidden.
    xfailed_req: list[str] = dataclasses.field(default_factory=list)

    @property
    def skip_mismatch(self) -> bool:
        return self.skip_checked and self.skip_expected != self.skip_got

    @property
    def ok(self) -> bool:
        return (not self.timed_out and self.exit_code == 0
                and not self.failed and not self.skip_mismatch)


def parse_transport(transport: str, r: CmdResult, skip_checked: bool,
                    xfail_extra: list[str] = ()) -> TransportResult:
    counts = {"passed": 0, "failed": 0, "xfailed": 0, "skipped": 0}
    for m in RE_COUNT.finditer(r.out):
        counts[m.group(2)] = int(m.group(1))
    failed = [m.group(2) for m in RE_RESULT.finditer(r.out)
              if m.group(1) in ("FAIL", "ERROR")]
    # --xfail (global) plus this box's per-target xfail: drop tolerated failures
    # from `failed` so they don't fail the cell.
    xfail_globs = [*XFAIL_GLOBS, *xfail_extra]
    xfailed_req = [f for f in failed
                   if any(fnmatch.fnmatch(f, g) for g in xfail_globs)]
    failed = [f for f in failed if f not in xfailed_req]
    exp = got = set()
    if skip_checked and RE_SKIP_HDR.search(r.out):
        em = RE_SKIP_EXP.search(r.out)
        gm = RE_SKIP_GOT.search(r.out)
        exp = _csv_set(em.group(1)) if em else set()
        got = _csv_set(gm.group(1)) if gm else set()
    rc = r.rc
    # runtests exits non-zero per failing test; if the only failures were
    # tolerated, clear the stale code so the cell reads OK (cf. retry_failed).
    if xfailed_req and not failed and rc != 0:
        rc = 0
    return TransportResult(transport, rc, r.timed_out, counts, failed,
                           skip_checked, exp, got, r.out, xfailed_req=xfailed_req)


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
        skip_csv = workflow_skip_for(t) if transport == "pipe" else None
        jobs = (args.jobs if args.jobs else
                (t.tcp_jobs if transport == "tcp" else t.pipe_jobs))
        cmd = test_script(t, transport, skip_csv, jobs)
        t0 = time.monotonic()
        r = run_on(t, cmd, timeout=2400)
        res.timings[transport] = time.monotonic() - t0
        tr = parse_transport(transport, r, skip_csv is not None, t.xfail)
        retry_failed(t, transport, tr, lambda names, tp=transport: run_on(
            t, test_script(t, tp, None, 1, only=names), timeout=1200))
        res.transports[transport] = tr
        log(f"[{t.name}] {transport} done "
            f"({'ok' if tr.ok else 'ISSUE'})")

    # Extra older-protocol passes (mirroring the workflow's check30/check29
    # steps): same stdio-pipe transport, but each protocol uses its own
    # check30/check29 skip list (a feature like xattrs/ACLs runs at proto 30 yet
    # skips at 29). Only targets that list `protocols` opt in; skipped under
    # --transport tcp (these are pipe runs).
    if t.protocols and "pipe" in args.transports:
        jobs = args.jobs if args.jobs else t.pipe_jobs
        for proto in t.protocols:
            label = f"proto{proto}"
            skip_csv = workflow_skip_for(t, f"check{proto}")
            cmd = test_script(t, "pipe", skip_csv, jobs, protocol=proto)
            t0 = time.monotonic()
            r = run_on(t, cmd, timeout=2400)
            res.timings[label] = time.monotonic() - t0
            tr = parse_transport(label, r, skip_csv is not None, t.xfail)
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
        tr = parse_transport("nonroot", r, skip_checked=False, xfail_extra=t.xfail)
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
    xfreq = [f"{res.target} / {transport}: {','.join(tr.xfailed_req)}"
             for res in results for transport in transports
             if (tr := res.transports.get(transport)) and tr.xfailed_req]
    if xfreq:
        print("==== XFAILED BY REQUEST (--xfail; failure tolerated, cell still OK) ====")
        for r in xfreq:
            print(f"    {r}")
        print("=" * 64)
    print(f"{len(results)} targets x {len(transports)} transports = {cells} cells: "
          f"{ok_cells} OK, {cells - ok_cells} not OK")
    return all_ok


def target_ok(res: TargetResult) -> bool:
    """True if this target produced no unexpected result at all -- reachable,
    pushed, built, and every pass it ran was OK."""
    if not (res.reachable and res.pushed and res.build_ok):
        return False
    return all(tr.ok for tr in res.transports.values())


def keep_on_fail(results: list[TargetResult], args,
                 chosen: list[Target]) -> list[str]:
    """--keep-on-fail: for every target that did NOT come back clean, save its
    full output locally and mark its remote run dir to survive the exit sweep.

    A fleet run is expensive (a full configure + build on ten machines), and the
    report only prints the names of failing tests -- so re-running was the only
    way to see WHY one failed, at the cost of another full run, on a race test
    that may not fail the same way twice.  Saving the raw output at the moment of
    failure, and keeping the tree that produced it, makes that re-run
    unnecessary.  Clean targets are untouched: they are still swept as usual.

    Returns human-readable lines describing what was kept, for the report."""
    failed = [r for r in results if not target_ok(r)]
    if not failed:
        return []
    root = Path(args.keep_on_fail).expanduser() / args.run_id
    notes: list[str] = []
    for res in failed:
        _retain_targets.add(res.target)
        d = root / _dirsafe(res.target)
        try:
            d.mkdir(parents=True, exist_ok=True)
            if res.error:
                (d / "error.txt").write_text(res.error + "\n")
            if res.build_log:
                (d / "build.log").write_text(res.build_log)
            for name, tr in res.transports.items():
                (d / f"{name}.log").write_text(tr.raw)
            saved = str(d)
        except OSError as e:
            saved = f"(could not write logs: {e})"
        notes.append(f"{res.target}: logs {saved}")
        # The remote tree is only meaningful if the run got far enough to make
        # one; an unreachable or un-pushed target has nothing to keep.
        if res.reachable and res.pushed:
            t = next((x for x in chosen if x.name == res.target), None)
            if t is not None:
                where = f"{t.ssh_host}:{t.builddir}" if t.ssh_host else t.builddir
                notes.append(f"{res.target}: run dir kept at {where}")
    return notes


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
# Names of targets whose run dir must survive the exit sweep (--keep-on-fail,
# populated once results are in). Everything else is removed as usual.
_retain_targets: set[str] = set()


def _dirsafe(name: str) -> str:
    """A target name reduced to what is safe in a remote path we later rm -rf.
    Anything outside [A-Za-z0-9._-] becomes '_', so a name cannot introduce a
    path separator, a shell metacharacter, or a leading dash."""
    return re.sub(r'[^A-Za-z0-9._-]', '_', name).lstrip('-') or 'target'


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
    own <base>-<run_id> dir, so a concurrent run's dir is never touched.

    Targets listed in _retain_targets are left alone -- that is --keep-on-fail
    holding a failed target's tree (build output plus the scratch trees the
    failing tests left) for a post-mortem."""
    global _cleanup_done
    with _cleanup_lock:
        if _cleanup_done or not _cleanup_targets:
            return
        _cleanup_done = True
        targets = [t for t in _cleanup_targets if t.name not in _retain_targets]
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


# sweep() counts a pattern, kills it (best effort; sudo -n retry for processes a
# root-running test left), then RE-counts after a settle so we report what
# actually died (KILLED = before-after) and flag any survivor (SURVIVED, sets
# fail) instead of claiming success when pkill couldn't reach it. The patterns
# use the pgrep self-exclusion trick -- 'r[e]name'/'det[a]ch'/'[l]ocalhost' match a
# real process's "rename"/"detach"/"localhost" but not the bracketed literal in this
# script's own argv (run_on passes the whole script as the remote argv), so we never
# signal ourselves. The client sweep catches an orphaned `rsync rsync://localhost:N/`
# left blocked on a read when its test was killed (no I/O timeout). @BASE@ is
# substituted with the target's run-dir prefix.
_CLEANUP_SCRIPT = r'''fail=0
# Cygwin signals are cooperative, so a process stuck in a Windows call ignores
# even SIGKILL and pkill cannot touch it -- exactly how an orphaned test rsyncd
# ends up squatting its port forever. Where taskkill exists, finish the job
# through Windows using the winpid (4th column of `ps -W`).
win_force() {
  command -v taskkill >/dev/null 2>&1 || return 0
  command -v ps >/dev/null 2>&1 || return 0
  for p in $(pgrep -f "$1" 2>/dev/null); do
    w=$(ps -W 2>/dev/null | awk -v x="$p" '$1==x {print $4}')
    [ -n "$w" ] && taskkill /F /PID "$w" >/dev/null 2>&1
  done
}
sweep() {
  command -v pgrep >/dev/null 2>&1 || return 0
  before=$(pgrep -f "$2" 2>/dev/null | wc -l | tr -d ' ')
  [ "$before" -gt 0 ] 2>/dev/null || return 0
  pkill -f "$2" 2>/dev/null
  sudo -n pkill -f "$2" 2>/dev/null
  sleep 1
  if [ "$(pgrep -f "$2" 2>/dev/null | wc -l | tr -d ' ')" -gt 0 ] 2>/dev/null; then
    win_force "$2"
    sleep 1
  fi
  after=$(pgrep -f "$2" 2>/dev/null | wc -l | tr -d ' ')
  killed=$((before - after))
  [ "$killed" -gt 0 ] 2>/dev/null && echo "KILLED $killed stray $1(s)"
  if [ "$after" -gt 0 ] 2>/dev/null; then
    echo "SURVIVED $after stray $1(s)"
    fail=1
  fi
}
sweep flipper 'r[e]name.*r[e]name.*r[e]name'
sweep daemon 'det[a]ch --address=127.0.0.1'
sweep client 'rsync://[l]ocalhost'
for d in @BASE@-*; do
  [ -e "$d" ] || continue
  if rm -rf -- "$d" 2>/dev/null || sudo -n rm -rf -- "$d" 2>/dev/null; then
    echo "REMOVED $d"
  else
    echo "FAILED $d"
    fail=1
  fi
done
exit $fail
'''


def cleanup_remnants(targets: list[Target]) -> int:
    """--cleanup mode: on each target, kill the stray processes a killed run can
    leave behind, then remove every <base>-* run dir, reporting what went.
    Returns a process exit code. Only suffixed run dirs are swept -- a bare
    <base> is left alone.

    A run that is SIGKILLed (or whose ssh drops) can strand two kinds of process
    on platforms without a parent-death backstop: the TOCTOU path-flipper (a
    busy `python -c` rename loop that pins a CPU) and an orphaned test rsyncd
    (`--no-detach --address=127.0.0.1`, which then squats its fixed port -- the
    very wedge claim_ports()' bind-probe now reports). Both are killed best
    effort (sudo -n retry for root-owned ones); a kill is verified by re-counting
    afterwards, and a process that survives is reported and fails the run.

    CAVEAT: the kill patterns are host-global, not scoped to a particular run, so
    --cleanup assumes no *other* fleettest run is active on the target -- it
    would also kill a concurrent run's flipper/daemon (and any manual `rsync
    --daemon --no-detach --address=127.0.0.1`). Run it between runs, not during
    one. Run dirs whose contents a root test owns are removed via a `sudo -n rm`
    fallback; only a dir that survives even that is a failure."""
    rc = 0
    for t in targets:
        base = t.builddir
        if _unsafe_builddir(base):
            log(f"[{t.name}] skipped (unsafe builddir {base!r})")
            continue
        # Structured markers (KILLED/SURVIVED/REMOVED/FAILED) keep the report
        # clean even though run_on() folds stderr into stdout.
        r = run_on(t, _CLEANUP_SCRIPT.replace("@BASE@", base), timeout=120)
        lines = r.out.splitlines()
        removed = [ln.split(" ", 1)[1] for ln in lines if ln.startswith("REMOVED ")]
        failed = [ln.split(" ", 1)[1] for ln in lines if ln.startswith("FAILED ")]
        killed = [ln.replace("KILLED ", "killed ", 1)
                  for ln in lines if ln.startswith("KILLED ")]
        survived = [ln.replace("SURVIVED ", "still alive: ", 1)
                    for ln in lines if ln.startswith("SURVIVED ")]
        msgs = killed[:]
        if removed:
            msgs.append("removed: " + " ".join(removed))
        if survived:
            rc = 1
            msgs += survived
        if failed:
            rc = 1
            msgs.append("could not remove (even with sudo): " + " ".join(failed))
        if r.rc not in (0, 1):
            rc = 1
            msgs.append(f"cleanup error rc={r.rc}: {r.out.strip()[:160]}")
        log(f"[{t.name}] " + ("; ".join(msgs) if msgs else "nothing to remove"))
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
    ap.add_argument("--keep-on-fail", nargs="?", const="fleettest-logs",
                    metavar="DIR",
                    help="for targets that came back with anything unexpected, "
                    "save the full build/test output under DIR/<run-id>/<target>/ "
                    "and keep that target's remote run dir (clean targets are "
                    "swept as usual). Makes a failure inspectable without "
                    "repeating the run. DIR defaults to ./fleettest-logs")
    ap.add_argument("--cleanup", action="store_true",
                    help="kill stray flippers/test daemons and remove stray "
                    "<builddir>-* run dirs (root-owned via sudo -n) on the "
                    "targets, then exit; run between runs, not during one "
                    "(kills are host-global)")
    ap.add_argument("--jobs", type=int, help="override -j for both transports")
    ap.add_argument("--full-tcp", action="store_true",
                    help="run the whole suite in the tcp pass. By default that "
                    "pass runs only the tests that can reach the daemon "
                    "transport, since the rest just repeat the pipe pass over "
                    "the same build")
    ap.add_argument("--timing", action="store_true",
                    help="report per-target wall-clock (push/build/test) to find "
                    "the slowest target")
    ap.add_argument("--repo", help="rsync source tree to build (default: cwd)")
    ap.add_argument("--testsuite-repo",
                    help="rsync tree to take runtests.py + testsuite/ from "
                    "(default: --repo). Build one tree and run another's test "
                    "suite against it, e.g. --repo ../rsync-v3.4 --testsuite-repo .")
    ap.add_argument("--fleet", help="fleet config JSON (default: ~/.fleettest.json, "
                    "else fleettest.json next to this script)")
    ap.add_argument("--skip", metavar="LIST",
                    help="comma-separated test-name globs to exclude from every "
                    "run on every target (passed to runtests as RSYNC_EXCLUDE; "
                    "the tests are not run). E.g. --skip 'chmod-option,prealloc*'")
    ap.add_argument("--xfail", metavar="LIST",
                    help="comma-separated test-name globs whose FAILURE is "
                    "tolerated: a matching fail is listed but does not make a "
                    "cell 'not OK'. For known stable-gap tests you still want to "
                    "run. E.g. --xfail 'chmod-option,preallocate,crtimes'")
    ap.add_argument("--list", action="store_true", help="list targets and exit")
    args = ap.parse_args()

    global SKIP_CSV, XFAIL_GLOBS, TIMING, FULL_TCP, BACKPORT_EXCLUDE
    SKIP_CSV = ",".join(s.strip() for s in (args.skip or "").split(",") if s.strip())
    XFAIL_GLOBS = [s.strip() for s in (args.xfail or "").split(",") if s.strip()]
    TIMING = args.timing
    FULL_TCP = args.full_tcp

    global REPO, WORKFLOWS, TESTSUITE_REPO
    REPO = Path(args.repo).resolve() if args.repo else Path.cwd()
    TESTSUITE_REPO = Path(args.testsuite_repo).resolve() if args.testsuite_repo else REPO
    # The expected-skip lists travel with the suite, so read workflows from the
    # tree that provides the tests.
    WORKFLOWS = TESTSUITE_REPO / ".github" / "workflows"

    # A tree that is OLDER than the suite being run against it -- a backport
    # branch under --testsuite-repo -- cannot pass tests for fixes and features
    # it does not carry, and cannot build the suite's newer unit-test helpers.
    # It declares those in its own testsuite/skiplist/backport.txt, which is
    # read from the BUILT tree, not the suite tree, because only the built tree
    # knows what it lacks.  The names are excluded outright (RSYNC_EXCLUDE)
    # rather than declared as expected skips: some of them fail rather than
    # skip, and an expected-skip list cannot describe a failure.
    bp = REPO / "testsuite" / "skiplist" / "backport.txt"
    if bp.is_file():
        names = [ln.split("#", 1)[0].strip() for ln in bp.read_text().splitlines()]
        names = [x for x in names if x]
        if names:
            SKIP_CSV = ",".join(x for x in ([SKIP_CSV] + names) if x)
            BACKPORT_EXCLUDE[:] = names
            print(f"[backport] excluding {len(names)} test(s) declared in "
                  f"{bp.relative_to(REPO)}")
    if not args.cleanup:
        # The Python test suite (runtests.py + testsuite/) comes from
        # TESTSUITE_REPO, so that is where runtests.py must live.  The build tree
        # (REPO) only has to be a buildable rsync source -- it may be an older
        # release whose runtests.py predates the Python suite, or lacks it.
        if not (TESTSUITE_REPO / "runtests.py").is_file():
            print(f"{TESTSUITE_REPO} has no runtests.py; run from inside a "
                  f"checkout or pass --testsuite-repo a tree with the Python "
                  f"test suite", file=sys.stderr)
            return 2
        if not (REPO / "rsync.h").is_file():
            print(f"{REPO} is not an rsync source tree (no rsync.h); "
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
    global TRANSPORTS
    TRANSPORTS = args.transports
    if "tcp" in args.transports and "pipe" not in args.transports and not FULL_TCP:
        log("note: --transport tcp is the only pass, so it runs the WHOLE suite "
            "(the daemon-only narrowing needs a pipe pass to cover the rest)")

    # Give this run its own build dir on every target so concurrent runs don't
    # collide, and name it after the target too, because two targets can share
    # one machine: <builddir>-<run_id>-<target>. Without the target part they
    # would push into the same tree, and the second would inherit the first's
    # config.h/Makefile and never reconfigure with its own flags. The base name
    # is still the prefix --cleanup globs.
    args.run_id = secrets.token_hex(3)
    for t in chosen:
        t.builddir = f"{t.builddir}-{args.run_id}-{_dirsafe(t.name)}"
    log(f"run {args.run_id}: build dir <target>:{chosen[0].builddir.rsplit('-', 1)[0]}-<target> "
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

        # --testsuite-repo: overlay another tree's runtests.py + testsuite/ onto
        # the built source (merge, no delete). Build REPO's rsync, but run
        # TESTSUITE_REPO's suite against it. The leftover .test files from REPO
        # are ignored by a Python runtests.py (it globs *_test.py).
        if TESTSUITE_REPO != REPO:
            ov = subprocess.run(
                f"git -C {TESTSUITE_REPO} archive HEAD -- runtests.py testsuite "
                f"| tar -x -C {staging}",
                shell=True, capture_output=True, text=True)
            if ov.returncode != 0:
                print(f"testsuite overlay archive failed: {ov.stderr}", file=sys.stderr)
                return 2

        # Tests that opt into the non-root pass (same for every target).
        args.nonroot_tests = discover_nonroot_tests(Path(staging) / "testsuite")

        # Targets are grouped by machine, and a machine's targets run one after
        # another. Two targets CAN name the same host -- an alternate-filesystem
        # variant of another target does exactly that -- and running those at
        # the same time breaks both: they would fight over the fixed ports the
        # daemon tests claim, and over any other host-global state. Different
        # machines still run concurrently, which is where the parallelism was.
        groups: dict[str, list[Target]] = {}
        for t in chosen:
            groups.setdefault(t.ssh_host or "<local>", []).append(t)
        for host, ts in groups.items():
            if len(ts) > 1:
                log(f"[{host}] {len(ts)} targets on one machine, run in "
                    f"sequence: {', '.join(x.name for x in ts)}")

        def run_group(ts: 'list[Target]') -> 'list[TargetResult]':
            out = []
            for t in ts:
                try:
                    out.append(run_target(t, args, staging))
                except Exception as e:  # never let one target kill the run
                    r = TargetResult(t.name)
                    r.reachable = False
                    r.error = f"harness exception: {e!r}"
                    out.append(r)
            return out

        results: list[TargetResult] = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(groups)) as ex:
            futs = {ex.submit(run_group, ts): ts for ts in groups.values()}
            for fut in concurrent.futures.as_completed(futs):
                ts = futs[fut]
                try:
                    results.extend(fut.result())
                except Exception as e:  # a whole group died: account for each
                    for t in ts:
                        r = TargetResult(t.name)
                        r.reachable = False
                        r.error = f"harness exception: {e!r}"
                        results.append(r)
    finally:
        subprocess.run(["rm", "-rf", staging])

    # Before the exit sweep runs: mark failed targets' run dirs to survive and
    # write their output locally, so the report can point at both.
    kept = keep_on_fail(results, args, chosen) if args.keep_on_fail else []

    all_ok = print_report(results, args, fleet)
    if kept:
        print("==== KEPT FOR POST-MORTEM (--keep-on-fail) ====")
        for k in kept:
            print(f"    {k}")
        print("=" * 64)
    if args.timing:
        print_timing(results)
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
